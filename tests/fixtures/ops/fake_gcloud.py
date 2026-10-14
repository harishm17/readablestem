"""A stateful stand-in for `gcloud`, for exercising deploy/deploy.sh offline.

Every call is appended to $FAKE_GCLOUD_DIR/calls.jsonl. A resource "exists" once a
create (or `secrets versions add`) for its kind has been seen, so a second run of the
script takes the "already exists" branches. Kinds are the command groups ("sql
instances", "scheduler jobs", ...); secrets are tracked per name. Secret values sent on
stdin are recorded as SHA-256 only. Alert policies are tracked by display name, like
`gcloud monitoring policies list --filter=displayName="..."` finds them, and their JSON
body is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

STATE = Path(os.environ["FAKE_GCLOUD_DIR"])
CRON_TOKEN = "fake-cron-token-0123456789abcdef"  # what `secrets versions access` returns
PER_NAME = {"secrets", "secrets versions", "logging metrics"}


def created() -> set[str]:
    path = STATE / "created.txt"
    return set(path.read_text().splitlines()) if path.exists() else set()


def mark(key: str) -> None:
    with (STATE / "created.txt").open("a") as fh:
        fh.write(key + "\n")


def flag(args: list[str], name: str) -> str:
    prefix = f"--{name}="
    return next((a[len(prefix) :] for a in args if a.startswith(prefix)), "")


def resource_key(words: list[str], verb: str) -> str:
    """("secrets", "describe", "cron-token") -> "secrets cron-token"; others: the group."""
    i = words.index(verb)
    kind = " ".join(words[:i])
    names = [w for w in words[i + 1 :] if w != "http"]
    return f"{kind} {names[0]}" if kind in PER_NAME and names else kind


def main() -> int:
    argv = sys.argv[1:]
    args = [a for a in argv if a != "--quiet" and not a.startswith(("--project=", "--billing"))]
    words = [a for a in args if not a.startswith("--")]
    record: dict = {"argv": argv}
    if env_file := flag(args, "env-vars-file"):
        record["env_file"] = Path(env_file).read_text()
    if words[:3] == ["secrets", "versions", "add"]:
        record["stdin_sha256"] = hashlib.sha256(sys.stdin.read().encode()).hexdigest()
    if policy_file := flag(args, "policy-from-file"):  # alert policies: keep the body
        record["policy"] = json.loads(Path(policy_file).read_text())
    with (STATE / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")

    fmt, have = flag(args, "format"), created()
    backups = STATE / "retained_backups.txt"  # Cloud SQL keeps 7 unless told otherwise
    if words[:2] == ["sql", "instances"] and (kept := flag(args, "retained-backups-count")):
        backups.write_text(kept)
    if words[:3] == ["secrets", "versions", "access"]:
        print(CRON_TOKEN)
    elif "describe" in words:
        if "retainedBackups" in fmt:
            print(backups.read_text() if backups.exists() else "7")
        elif "connectionName" in fmt:
            print("proj:us-central1:readablestem-db")
        elif "projectNumber" in fmt:
            print("123456789")
        elif "status.url" in fmt:
            print("https://readablestem-abc123-uc.a.run.app")
        elif resource_key(words, "describe") not in have:
            return 1
    elif words[:3] == ["monitoring", "policies", "list"]:  # alert policies, by display name
        if f"monitoring policies {flag(args, 'filter')}" in have:
            print("projects/p/alertPolicies/existing")
    elif "list" in words or "list-configs" in words:
        verb = "list" if "list" in words else "list-configs"
        if resource_key(words, verb) in have:
            print(f"projects/p/{words[0]}/existing")
    elif words[:3] == ["monitoring", "policies", "create"]:
        mark(f'monitoring policies displayName="{record["policy"]["displayName"]}"')
    elif "create" in words:
        mark(resource_key(words, "create"))
        if "name" in fmt:
            print(f"projects/p/{words[0]}/new-1")
    elif words[:3] == ["secrets", "versions", "add"]:
        mark(resource_key(words, "add"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
