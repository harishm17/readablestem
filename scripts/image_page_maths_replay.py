"""The scanned papers' held-out recording, replayed with and without "maths always required".

    uv run python scripts/image_page_maths_replay.py --arm off
    uv run python scripts/image_page_maths_replay.py --arm on

ADR 0007, point 12: on an image page every block that holds maths is a required decision. This
replays the recorded answers of the real-world held-out papers as scans (docs/eval-image-pages.md,
"Held-out check"; recorded with image page prompt `image_page-632b4d29`, kept at
`.local/prompts/v1.md`, and `claude-opus-5-5`) through the real pipeline with
`IMAGE_PAGES_MATHS_ALWAYS_REQUIRED` false or true. `--answers-offline`: a request the file cannot
answer is refused and nothing is ever sent. The equation step's recorded answers belong to an older
equation prompt and the first reading's boxes to an older builder, so most equation requests are
refused: the primary score is not comparable with the docs, only between the two arms; the number
this measures is the required decisions per page.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
OUT = REPO / ".local" / "image-pages" / "maths"
PAPERS = Path.home() / ".cache" / "readablestem" / "realworld" / "gold" / "heldout"
ANSWERS = REPO / ".local" / "image-pages" / "heldout" / "opus" / "answers.jsonl"
PROMPT = REPO / ".local" / "prompts" / "v1.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm", choices=("off", "on"), required=True)
    args = parser.parse_args()
    os.environ.update(
        CLAUDE_SECOND_CHECK="off",
        FEATURE_IMAGE_PAGES="true",
        IMAGE_PAGES_MODEL="claude-opus-5-5",
        IMAGE_PAGES_RECOVER_OMISSIONS="false",
        IMAGE_PAGES_MATHS_ALWAYS_REQUIRED="true" if args.arm == "on" else "false",
    )
    os.environ.pop("IMAGE_PAGES_DPI", None)
    os.environ.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.models import prompts
    from readablestem.pipeline import image_page

    text = PROMPT.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    fixed = prompts.Prompt(name="image_page", version=f"image_page-{digest}", text=text)
    image_page.load_prompt = lambda name: fixed
    image_page.PROMPT_VERSION = fixed.version
    import realworld_scanned as rs  # type: ignore[import-not-found]

    directory = OUT / args.arm
    directory.mkdir(parents=True, exist_ok=True)
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(ANSWERS), "--answers-offline", "--out", str(directory / "report.json"),
        "--items", str(directory / "items.jsonl"), "--max-usd", "1",
        "--cache", str(directory / "cache"),
    ]  # fmt: skip
    ns = build_parser().parse_args(argv)
    return eval_cli._run_pipeline(
        ns,
        PAPERS,
        directory / "report.json",
        gold_label=f"scanned papers, held-out recording ({args.arm})",
        media_cls=rs.ScannedRealWorld,
        cache_dir=directory / "cache",
        openstax_cache=directory / "cache",
    )


if __name__ == "__main__":
    sys.exit(main())
