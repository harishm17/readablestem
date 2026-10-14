"""Record the chatbot arms of a held-out run into the work folder, one arm and one number of
runs at a time, before `a11y-stem proof --add-systems chatbot` adds them to the recorded run.

    uv run --env-file ENV_FILE python proof/record-chat-runs.py \
        BUNDLE WORK ARM RUNS CAP_USD [--provider anthropic]

It calls `proof.chatbot.record_arms`, which `--add-systems chatbot` calls too, and writes the
same files into `WORK/systems-out/chatbot/`; the work folder is the one both commands share,
so the CLI then finds every job recorded, pays nothing for them, judges the figure
descriptions and adds the arm to the bundle. The point of the separate step is that the number
of runs can be decided from the spend so far (Addendum 6, section 7). CAP_USD limits this
start only, as in the CLI; a job already recorded is skipped. The key is read from the
environment and never printed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from a11y_stem.proof import chatbot
from a11y_stem.proof.bundle import Bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bundle", type=Path, help="the recorded run (a11y-stem's, finished)")
    parser.add_argument("work", type=Path, help="the work folder of the recording")
    parser.add_argument("arm", choices=sorted(chatbot.ARM_BY_ID))
    parser.add_argument("runs", type=int, help="runs of the arm to make (at most the arm's own)")
    parser.add_argument("cap_usd", type=float, help="spend cap of this start")
    parser.add_argument("--provider", default="anthropic", choices=("openai", "anthropic"))
    args = parser.parse_args()

    bundle = Bundle.open(args.bundle)
    bundle.require_intact()
    pdfs = {e["section"]: bundle.path(e["file"]) for e in bundle.pdfs}
    out = args.work / "systems-out" / "chatbot"
    out.mkdir(parents=True, exist_ok=True)
    arm = chatbot.ARM_BY_ID[args.arm]
    if arm.provider != args.provider:
        parser.error(f"{arm.id} runs on {arm.provider}, not on {args.provider}")
    before = chatbot.recorded_cost(out, [arm.id])
    summary = chatbot.record_arms(
        pdfs,
        out,
        arms=[arm],
        max_usd=args.cap_usd,
        client=chatbot.make_client(args.provider),
        runs=args.runs,
    )
    print("SUMMARY", summary)
    print("ARM COST TOTAL", chatbot.recorded_cost(out, [arm.id]), "before this start", before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
