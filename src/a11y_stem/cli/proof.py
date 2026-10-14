"""CLI: `a11y-stem proof`, one command that writes PROOF.md.

    a11y-stem proof                      replay the recorded run: no key, no network, no cost
    a11y-stem proof --set ID             another proof set (see --list)
    a11y-stem proof --verify-sources     also fetch the answer key from OpenStax and print
                                         the PDFs again (network, Chromium)
    a11y-stem proof --live --max-usd N   call the models again (OPENAI_API_KEY), record a
                                         new bundle, then replay it to write PROOF.md
    a11y-stem proof --live --provider anthropic --max-usd N --record DIR --out FILE
                                         the same with Claude (ANTHROPIC_API_KEY; models
                                         from ANTHROPIC_MAIN_MODEL / ANTHROPIC_CHEAP_MODEL
                                         or --main-model / --cheap-model); --out keeps
                                         PROOF.md, the OpenAI recording's report, as it is
    a11y-stem proof --add-systems chatbot --max-usd N [--bundle DIR]
                                         record the chatbot arms into a recorded run
                                         (--provider anthropic: the Claude arms, with the
                                         figure judge on claude-opus-5-5; --arms and
                                         --chat-runs choose what is run)
    a11y-stem proof --add-systems parsers [--bundle DIR]
                                         record the open parsers (hours; resumable)
    a11y-stem proof --check              check the pins and the bundle's hashes, run nothing

Exit codes: 0 done; 1 incomplete (`--require-complete`) or a run stopped by its cap; 2 bad
usage or setup; 3 a pin or a bundle file does not match; 4 the replay does not reproduce
the recorded numbers (drift); 5 the replay needed an answer the bundle does not hold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

from a11y_stem.proof.errors import ProofError


def _error(message: str, code: int = 2) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not number > 0 or math.isinf(number):
        raise argparse.ArgumentTypeError(f"must be a positive amount, got {value}")
    return number


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _iso_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _say(message: str) -> None:
    print(message, flush=True)


def _cmd_proof(args: argparse.Namespace) -> int:
    from a11y_stem.cli.main import project_root
    from a11y_stem.proof import engine, registry

    root = project_root()
    try:
        if args.list:
            default = registry.default_set_id(root)
            for pset in registry.list_sets(root):
                kind = "held out" if pset.held_out else "development"
                mark = " (default)" if pset.id == default else ""
                print(f"{pset.id}{mark}: {kind}, {len(pset.sections)} sections, {pset.title}")
            return 0
        opts = engine.ProofOptions(
            root=root,
            set_id=args.set,
            bundle_dir=args.bundle,
            out=args.out,
            work_dir=args.work,
            live=args.live,
            record_dir=args.record,
            max_usd=args.max_usd,
            max_calls=args.max_calls,
            section_workers=args.section_workers,
            fake=args.fake,
            confirm_signoff=args.confirm_signoff,
            allow_dirty=args.allow_dirty,
            keep_finished=args.keep_finished,
            resume=args.resume,
            allow_drift=args.allow_drift,
            allow_misses=args.allow_misses,
            verify_sources=args.verify_sources,
            require_complete=args.require_complete,
            n_boot=args.bootstrap,
            date=args.date,
            provider=args.provider,
            main_model=args.main_model,
            cheap_model=args.cheap_model,
            chat_runs=args.chat_runs,
        )
        if args.add_systems:
            engine.add_systems(opts, args.add_systems, _say, arms=args.arms or None)
            return 0
        if args.check:
            for check in engine.check_proof(opts):
                print(f"{check.status:<8} {check.name}: {check.detail}")
            return 0
        result = engine.run_proof(opts, _say)
    except ProofError as exc:
        return _error(str(exc), exc.code)
    if result.out is not None:
        print(f"wrote {_shown(result.out, root)}")
    for line in result.not_wired:
        print(f"not computed (stub): {line}")
    if result.drift:
        print(f"warning: the replay differs from the recording in {len(result.drift)} place(s)")
    return result.exit_code


def _shown(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def register(sub: argparse._SubParsersAction) -> None:
    today = dt.date.today().isoformat()
    proof = sub.add_parser(
        "proof",
        help="write PROOF.md: replay the recorded run (default) or re-run the models (--live)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    proof.add_argument("--set", help="proof set id (default: the repository's default)")
    proof.add_argument("--list", action="store_true", help="list the proof sets and exit")
    proof.add_argument("--check", action="store_true", help="check pins and hashes, run nothing")
    proof.add_argument("--bundle", type=Path, help="recorded run to replay (default: the set's)")
    proof.add_argument("--out", type=Path, help="report path (default: PROOF.md)")
    proof.add_argument("--work", type=Path, help="prints and caches (default: outside the repo)")
    proof.add_argument(
        "--verify-sources",
        action="store_true",
        help="fetch the answer key from OpenStax and print the PDFs again, and compare them "
        "with the pins and the recorded PDFs (network, Chromium)",
    )
    proof.add_argument("--require-complete", action="store_true", help="exit 1 if a part is a stub")
    proof.add_argument(
        "--allow-drift",
        action="store_true",
        help="write the report even if this checkout is not the code that made the recording "
        "or its replay differs; the report says so",
    )
    proof.add_argument(
        "--allow-misses", action="store_true", help="replay: continue past missing answers"
    )
    proof.add_argument("--bootstrap", type=_positive_int, default=10_000, help=argparse.SUPPRESS)
    live = proof.add_argument_group("live run (calls the models and costs money)")
    live.add_argument("--live", action="store_true", help="re-call the models and record a bundle")
    live.add_argument("--max-usd", type=_positive_float, help="hard spend cap for the whole run")
    live.add_argument("--max-calls", type=_positive_int, default=5000)
    live.add_argument(
        "--section-workers",
        type=_positive_int,
        default=1,
        help="document task: sections converted at once (default 1, as the registered runs "
        "were made). More shortens a live run; the sections' wall-clock times are then not "
        "recorded, because each would include its wait for the others",
    )
    live.add_argument(
        "--provider",
        choices=("openai", "anthropic"),
        default="openai",
        help="model provider of the live run (default openai; anthropic needs "
        "ANTHROPIC_API_KEY). The recording stores it, and a replay uses what it says",
    )
    live.add_argument("--main-model", metavar="ID", help="main model of --provider")
    live.add_argument("--cheap-model", metavar="ID", help="cheap model of --provider")
    live.add_argument("--record", type=Path, help="where to write the new bundle")
    live.add_argument("--date", type=_iso_date, default=today, help="recording date")
    live.add_argument("--fake", action="store_true", help="offline fake client; not for held out")
    live.add_argument(
        "--confirm-signoff",
        action="store_true",
        help="a held-out set: the decisions the set lists are made",
    )
    live.add_argument(
        "--add-systems",
        choices=("chatbot", "parsers"),
        help="record the chatbot arms (needs --max-usd; the cap covers the figure judge too) "
        "or the open parsers into an existing recorded run (--bundle), then stop",
    )
    live.add_argument("--arms", nargs="*", help="with --add-systems: only these arms")
    live.add_argument(
        "--chat-runs",
        type=_positive_int,
        metavar="N",
        help="with --add-systems chatbot: make N runs of each arm, not the arm's own number "
        "(the recorded run says how many were made)",
    )
    live.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted recording in --record: runs that finished are read back "
        "(their spend counts against --max-usd) and the run that was running is made again",
    )
    live.add_argument(
        "--keep-finished",
        action="store_true",
        help="if the spend cap stops the run, keep the runs that finished and label the "
        "report incomplete (the default is to write no bundle)",
    )
    live.add_argument("--allow-dirty", action="store_true", help="record from a dirty checkout")
    proof.set_defaults(func=_cmd_proof)
