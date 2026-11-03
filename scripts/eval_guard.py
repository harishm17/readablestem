"""Fail a pull request that changes a model or a prompt without re-running the eval.

    python3 scripts/eval_guard.py --base origin/main [--head HEAD] [--labels-json '["x"]']

Run by `.github/workflows/eval-guard.yml`. The CI eval gate compares the committed
`data/eval/latest-<task>.json` with its baseline, so a change to a model constant or a
prompt cannot fail it by itself: nothing re-measures. This guard closes that hole. A change
is "a model constant" when an assignment to a name containing `model` in
`src/readablestem/config.py` has another value than on the base; it is "a prompt" when a
`.md` file under `src/readablestem/models/prompts/` is added, changed or removed. Either
needs the matching `latest-*.json` reports in the same pull request (`docs/eval.md`, "How
to rerun").

Opt-out for a change that does not alter what a model is asked or which model answers (a
rename, a comment, a typo that is not in the prompt text the model sees): the label
`eval-unchanged` on the pull request, or a commit trailer `Eval-Unchanged: <reason>` (the
reason is required) in any commit of it. The opt-out is printed in the log so a reviewer
sees it.

Standard library only. Exit 0: fine or opted out. Exit 1: reports missing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence

CONFIG = "src/readablestem/config.py"
PROMPTS_DIR = "src/readablestem/models/prompts/"
EVAL_DIR = "data/eval/"
OPT_OUT_LABEL = "eval-unchanged"
TRAILER = re.compile(r"^Eval-Unchanged:[ \t]*(\S.*)$", re.IGNORECASE | re.MULTILINE)

# An assignment whose name has "model" in it, with its right-hand side up to a comment.
_MODEL_ASSIGNMENT = re.compile(
    r"^[ \t]*(?P<name>\w*model\w*)[ \t]*(?::[^=\n]+)?=[ \t]*(?P<value>[^#\n]+?)[ \t]*(?:#.*)?$",
    re.IGNORECASE | re.MULTILINE,
)

ANTHROPIC_REPORTS = (
    "latest-equations.json",
    "latest-headings.json",
    "latest-figures.json",
    "latest-document.json",
)
# Prompt file (without .md) -> the committed reports that measure it. A prompt that is not
# listed is measured by the document task, which runs the whole pipeline.
PROMPT_REPORTS: dict[str, tuple[str, ...]] = {
    "equation": ("latest-equations.json", "latest-document.json"),
    "equation_line": ("latest-equations.json", "latest-document.json"),
    "equation_notation": ("latest-equations.json", "latest-document.json"),
    "check_equation": ("latest-equations.json",),
    "figure": ("latest-figures.json", "latest-document.json"),
    "figure_exam": ("latest-figures.json", "latest-document.json"),
    "figure_long": ("latest-figures.json", "latest-document.json"),
    "check_figure": ("latest-figures.json",),
    "judge_figure": ("latest-figures.json",),
    "judge_long_description": ("latest-figures.json",),
    "table": ("latest-tables-local.json", "latest-document.json"),
}
DEFAULT_PROMPT_REPORTS = ("latest-document.json",)
# Model constant (name, lower case) -> the reports that record those models. The OpenAI
# pair is recorded by the tables report (a local run, no model); the Claude pair by the
# model tasks. Any other model setting is measured by the document task.
MODEL_REPORTS: dict[str, tuple[str, ...]] = {
    "main_model": ("latest-tables-local.json",),
    "cheap_model": ("latest-tables-local.json",),
    "anthropic_main_model": ANTHROPIC_REPORTS,
    "anthropic_cheap_model": ANTHROPIC_REPORTS,
}
DEFAULT_MODEL_REPORTS = ("latest-document.json",)


def model_assignments(source: str) -> dict[str, set[str]]:
    """Name (lower case) -> every value it is assigned in `source`."""
    found: dict[str, set[str]] = {}
    for match in _MODEL_ASSIGNMENT.finditer(source):
        value = re.sub(r"\s+", " ", match.group("value")).strip()
        found.setdefault(match.group("name").lower(), set()).add(value)
    return found


def changed_model_names(base_source: str, head_source: str) -> list[str]:
    """Names of model assignments added, removed or given another value."""
    before, after = model_assignments(base_source), model_assignments(head_source)
    return sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )


def prompt_name(path: str) -> str | None:
    """`equation` for `src/readablestem/models/prompts/equation.md`, else None."""
    if not path.startswith(PROMPTS_DIR) or not path.endswith(".md"):
        return None
    rest = path[len(PROMPTS_DIR) : -len(".md")]
    return rest if rest and "/" not in rest else None


def required_reports(
    changed_files: Iterable[str], config_names_changed: Sequence[str]
) -> dict[str, list[str]]:
    """Report file name -> the reasons it must change, for this change set."""
    needed: dict[str, list[str]] = {}
    for path in sorted(set(changed_files)):
        name = prompt_name(path)
        if name is None:
            continue
        for report in PROMPT_REPORTS.get(name, DEFAULT_PROMPT_REPORTS):
            needed.setdefault(report, []).append(f"prompt {name}.md changed")
    for name in config_names_changed:
        for report in MODEL_REPORTS.get(name, DEFAULT_MODEL_REPORTS):
            needed.setdefault(report, []).append(f"{CONFIG}: {name} changed")
    return needed


def opt_out(labels: Iterable[str], commit_messages: Iterable[str]) -> str | None:
    """Why the check is waived (the label, or the trailer's reason), or None."""
    if any(label.strip().lower() == OPT_OUT_LABEL for label in labels):
        return f"label {OPT_OUT_LABEL}"
    for message in commit_messages:
        found = TRAILER.search(message)
        if found:
            return f"commit trailer Eval-Unchanged: {found.group(1).strip()}"
    return None


def check(
    changed_files: Sequence[str],
    base_config: str,
    head_config: str,
    labels: Iterable[str] = (),
    commit_messages: Iterable[str] = (),
) -> tuple[int, list[str]]:
    """(exit code, lines to print)."""
    names = changed_model_names(base_config, head_config) if CONFIG in changed_files else []
    needed = required_reports(changed_files, names)
    if not needed:
        return 0, ["eval guard: no model constant or prompt changed; nothing to check"]
    updated = {f[len(EVAL_DIR) :] for f in changed_files if f.startswith(EVAL_DIR)}
    missing = {report: why for report, why in needed.items() if report not in updated}
    if not missing:
        return 0, ["eval guard: every report this change needs is updated: " + ", ".join(needed)]
    waiver = opt_out(labels, commit_messages)
    if waiver:
        lines = [f"eval guard: waived by {waiver}", "reports not updated:"]
        lines += [f"  {EVAL_DIR}{report}: {', '.join(why)}" for report, why in missing.items()]
        return 0, lines
    lines = ["eval guard: this change alters a model or a prompt but not its eval report(s):"]
    lines += [f"  {EVAL_DIR}{report}: {', '.join(why)}" for report, why in sorted(missing.items())]
    lines += [
        "",
        "Re-run the task(s), promote the report `--as latest` and commit it with the change:",
        'docs/eval.md, section "How to rerun" (`readablestem eval pipeline`, then',
        "`readablestem eval promote ... --as latest`).",
        "",
        "If the change does not alter what a model is asked or which model answers (a rename,",
        f"a comment), add the label `{OPT_OUT_LABEL}` to the pull request, or a commit trailer",
        '`Eval-Unchanged: <reason>`. Both are documented in docs/eval.md, "Eval guard".',
    ]
    return 1, lines


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"error: git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def _show(ref: str, path: str) -> str:
    done = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="the base ref, e.g. origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--labels-json", default="[]", help="JSON list of the pull request labels")
    args = parser.parse_args(argv)
    try:
        labels = [str(x) for x in json.loads(args.labels_json)]
    except ValueError:
        parser.error("--labels-json must be a JSON list of strings")
    # Three dots: what the pull request changes since it branched from the base.
    changed = _git("diff", "--name-only", f"{args.base}...{args.head}").splitlines()
    base = _git("merge-base", args.base, args.head).strip()
    messages = _git("log", "--format=%B%x00", f"{args.base}..{args.head}").split("\x00")
    code, lines = check(changed, _show(base, CONFIG), _show(args.head, CONFIG), labels, messages)
    print("\n".join(lines))
    if code:
        print(
            "::error::eval guard: re-run the eval for the changed model or prompt "
            "(docs/eval.md, How to rerun)"
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
