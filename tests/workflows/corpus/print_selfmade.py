"""Print a self-made corpus file again from its source, with the options in the manifest.

    uv run python tests/workflows/corpus/print_selfmade.py ID OUTDIR

HTML is printed by Playwright's Chromium, LaTeX by pdflatex. The Word-made file is rebuilt
by pandoc and LibreOffice Writer. The bytes differ from run to run; see README.md.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from readablestem.eval import corpus

FURNITURE = "font-size:8pt;width:100%;font-family:Liberation Sans"
FOOTER = (
    f"<div style='{FURNITURE};text-align:center'>Page <span class='pageNumber'></span> of "
    "<span class='totalPages'></span></div>"
)


def chromium(source: Path, target: Path, options: dict) -> None:
    from playwright.sync_api import sync_playwright

    margin = options.get("margin")
    if isinstance(margin, str):
        margin = dict.fromkeys(("top", "bottom", "left", "right"), margin)
    header, footer = options.get("header"), options.get("footer")
    settings: dict = {
        key: options[key]
        for key in ("format", "width", "height", "landscape", "print_background", "tagged")
        if key in options
    }
    if margin:
        settings["margin"] = margin
    if header or footer:
        settings["display_header_footer"] = True
        top = f"<div style='{FURNITURE};padding:0 0.8in'>{header}</div>"
        settings["header_template"] = top if header else "<span></span>"
        settings["footer_template"] = FOOTER if footer else "<span></span>"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(source.resolve().as_uri())
        page.pdf(path=str(target), **settings)
        browser.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    entry = corpus.load_manifest()[argv[0]]
    if entry.kind != "committed" or not entry.own or entry.made is None:
        print(f"{entry.id} was not printed here")
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    target = out / Path(entry.path).name
    source = corpus.CORPUS / entry.made["from"][0]
    how = entry.made["how"]
    if how == "chromium":
        chromium(source, target, entry.made.get("options", {}))
    elif how.startswith("pdflatex"):
        shutil.copyfile(source, out / source.name)
        command = ["pdflatex", "-interaction=nonstopmode", source.name]
        subprocess.run(command, cwd=out, check=True, capture_output=True)
    else:
        word = out / source.with_suffix(".docx").name
        subprocess.run(["pandoc", str(source), "-o", str(word)], check=True)
        command = ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out), str(word)]
        subprocess.run(command, check=True, capture_output=True)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
