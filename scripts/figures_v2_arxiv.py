"""Figure gold v2, arXiv strand: find pgfplots charts with inline data in CC BY papers.

    uv run python scripts/figures_v2_arxiv.py scan --limit 200
    uv run python scripts/figures_v2_arxiv.py render

`scan` takes the first LIMIT papers of the seeded order of `data/realworld/pool.json` (CC BY
3.0 and 4.0 records only; the licence is read from the pool, which came from the OAI record),
downloads each e-print into its own folder under CACHE, reads the TeX files and writes
`data/gold-figures-v2/pool-arxiv.json`: one row per chart that passes the gates of
`gold/figures_v2_arxiv.py`, with the plotted data. Downloads wait 3 seconds. `render` compiles
each hit
with tectonic and rasterises it with pdftoppm into `data/gold-figures-v2/arxiv/<id>/`. No model
is called.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

from readablestem.gold import figures_v2 as fv
from readablestem.gold import figures_v2_arxiv as fa

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gold-figures-v2"
POOL = ROOT / "data" / "realworld" / "pool.json"
CACHE = Path.home() / ".cache" / "readablestem" / "figures-v2" / "arxiv"
UA = "readablestem-eval/0.1 (research; contact: harishm6250@gmail.com)"
MAX_BYTES = 40_000_000
SAFE_ID = re.compile(r"^\d{4}\.\d{4,5}$")


def ordered_papers() -> list[dict]:
    pool = json.loads(POOL.read_text("utf-8"))
    ok = [
        c
        for c in pool["candidates"]
        if fv.licence_ok("arxiv", c["license"]) and SAFE_ID.match(c["id"])
    ]
    return sorted(ok, key=lambda c: fv.stable_key(fv.SEED, "arxiv-paper", c["id"]))


def fetch_eprint(paper_id: str, folder: Path) -> list[Path]:
    """Download one e-print into `folder` and return the TeX files in it. A tar is extracted
    with the `data` filter (no links, no absolute paths); a plain gzip is one TeX file."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / ".done"
    if not marker.exists():
        request = urllib.request.Request(
            f"https://arxiv.org/e-print/{paper_id}", headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("e-print over the size cap")
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as tar:
                members = [m for m in tar.getmembers() if m.name.endswith(".tex") and m.isfile()]
                for member in members:
                    tar.extract(member, folder, filter="data")
        except tarfile.TarError:
            with contextlib.suppress(OSError):
                (folder / "main.tex").write_bytes(gzip.decompress(body))
        marker.write_text("ok")
    return sorted(folder.rglob("*.tex"))


def cmd_scan(args: argparse.Namespace) -> int:
    rows: list[dict] = []
    scanned = 0
    for paper in ordered_papers()[: args.limit]:
        folder = CACHE / paper["id"]
        fresh = not (folder / ".done").exists()
        try:
            files = fetch_eprint(paper["id"], folder)
        except Exception as exc:
            print(f"{paper['id']}: skipped ({type(exc).__name__})", file=sys.stderr)
            continue
        scanned += 1
        for tex_path in files:
            text = tex_path.read_text("utf-8", errors="ignore")
            if "addplot" not in text:
                continue
            for index, figure in enumerate(fa.find_plots(text)):
                name = f"{tex_path.stem}-{index}"
                rows.append(
                    {
                        "id": f"arxiv/{paper['id']}/{name}",
                        "paper": paper["id"],
                        "title": paper["title"],
                        "authors": paper.get("authors", ""),
                        "licence_name": paper["licence_name"],
                        "licence_url": paper["license"],
                        "file": tex_path.relative_to(folder).as_posix(),
                        "kind": figure.kind,
                        "n_series": len(figure.series),
                        "n_points": len(figure.series[0].points),
                    }
                )
        if fresh:
            time.sleep(3)
        if scanned % 25 == 0:
            print(f"scanned {scanned}, charts {len(rows)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pool = {
        "set": "figures-v2",
        "source": "arxiv",
        "seed": fv.SEED,
        "papers_scanned": scanned,
        "rule": "first N of the seeded order of data/realworld/pool.json, CC BY 3.0/4.0 only",
        "candidates": rows,
    }
    (OUT / "pool-arxiv.json").write_text(
        json.dumps(pool, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("papers", scanned, "charts", len(rows))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    pool = json.loads((OUT / "pool-arxiv.json").read_text("utf-8"))
    kept = []
    for row in pool["candidates"]:
        folder = CACHE / row["paper"]
        text = (folder / row["file"]).read_text("utf-8", errors="ignore")
        index = int(row["id"].rsplit("-", 1)[1])
        figure = fa.find_plots(text)[index]
        target = OUT / "arxiv" / row["paper"]
        target.mkdir(parents=True, exist_ok=True)
        stem = row["id"].rsplit("/", 1)[1]
        tex_file = target / f"{stem}.tex"
        tex_file.write_text(figure.standalone(), encoding="utf-8")
        result = subprocess.run(
            ["tectonic", "--outdir", str(target), str(tex_file)],
            capture_output=True,
            timeout=180,
            check=False,
        )
        pdf = target / f"{stem}.pdf"
        if result.returncode != 0 or not pdf.exists():
            print(f"{row['id']}: does not compile", file=sys.stderr)
            tex_file.unlink(missing_ok=True)
            continue
        subprocess.run(
            ["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf), str(target / stem)],
            check=True,
        )
        pdf.unlink()
        png = target / f"{stem}.png"
        row["image_sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
        kept.append(row)
        print(f"{row['id']}: rendered")
    pool["rendered"] = len(kept)
    pool["candidates"] = kept
    (OUT / "pool-arxiv.json").write_text(
        json.dumps(pool, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("render")
    p.set_defaults(func=cmd_render)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
