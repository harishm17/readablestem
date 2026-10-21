"""Real-world set, step 2: fetch candidates in the seeded order, make gold, split.

    uv run python scripts/realworld_build.py build --order data/realworld/order-SEED.json \
        --target 40 --held-out 15

For each candidate of the order, until `--target` papers have been accepted: download the arXiv
PDF and the source (e-print), read the source (`gold/arxiv_tex.py`), place the equations on the
PDF's pages, and accept the paper if it passes the gates (a source with a main .tex file, at most
10% unconvertible equations, at least 4 usable pages). Every candidate and its outcome is written
to `data/realworld/candidates.json`; a skipped candidate is never replaced by choice.

The accepted papers are split by the seeded hash, stratified by style, into a development part and a
held-out part (`--held-out` papers). Gold (JSON lines), the cut PDFs (the chosen pages of the arXiv
PDF) and the downloads go under ROOT (default ~/.cache/readablestem/realworld; outside the
repository). The repository gets only manifests: ids, licences, hashes, chosen pages, counts.

Downloaded files are untrusted data: archives are unpacked with tarfile's `data` filter into a
folder of their own, and nothing in them is executed or compiled.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path

import httpx

from readablestem.gold import arxiv_corpus as ac
from readablestem.gold import arxiv_gold as ag
from readablestem.gold import arxiv_tex as tex

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO_DATA = Path(__file__).resolve().parent.parent / "data" / "realworld"
MAX_DOWNLOAD = 60_000_000
MAX_UNCONVERTIBLE = 0.10


class Skip(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def download(client: httpx.Client, url: str, target: Path, pause: float) -> bytes:
    if target.is_file():
        return target.read_bytes()
    for _ in range(4):
        response = client.get(url, timeout=180, follow_redirects=True)
        if response.status_code == 200:
            data = response.content
            if len(data) > MAX_DOWNLOAD:
                raise Skip("download_too_large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            time.sleep(pause)
            return data
        if response.status_code in (429, 503):
            time.sleep(float(response.headers.get("retry-after", 30)) + 1)
            continue
        time.sleep(pause)
        raise Skip(f"http_{response.status_code}", url)
    raise Skip("http_retries", url)


def unpack(data: bytes, folder: Path) -> None:
    """Source archive (tar.gz), a single gzipped .tex, or a plain tar, into the empty `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"%PDF":
        raise Skip("source_is_pdf")
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    except OSError as exc:
        raise Skip("bad_archive", str(exc)) from None
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(folder, filter="data")
        return
    except tarfile.TarError:
        pass
    (folder / "main.tex").write_bytes(raw)


def process(
    record: dict, root: Path, client: httpx.Client, pause: float, seed: str
) -> tuple[ag.Analysis, list[int], dict]:
    slug = ag.paper_slug(record["id"])
    base = root / "papers" / slug
    pdf_bytes = download(client, f"https://arxiv.org/pdf/{record['id']}", base / "paper.pdf", pause)
    eprint = download(
        client, f"https://arxiv.org/e-print/{record['id']}", base / "e-print.bin", pause
    )
    src = base / "src"
    if not src.is_dir():
        unpack(eprint, src)
    sources = tex.read_sources(src)
    if not sources:
        raise Skip("no_tex_files")
    try:
        texts = ag.page_texts_of(base / "paper.pdf")
    except Exception as exc:
        raise Skip("unreadable_pdf", type(exc).__name__) from None
    if not ac.MIN_PAGES <= len(texts) <= ac.MAX_PAGES:
        raise Skip("pdf_page_count", str(len(texts)))
    if sum(len(t) for t in texts) / len(texts) < 800:
        raise Skip("no_text_layer")
    result = ag.analyse(sources, texts)
    if isinstance(result, str):
        raise Skip(result)
    if result.unconvertible_share > MAX_UNCONVERTIBLE:
        raise Skip("unconvertible_equations", f"{result.unconvertible_share:.2f}")
    pages = ag.choose_pages(result.usable, seed, record["id"])
    if not pages:
        raise Skip("fewer_than_4_usable_pages", str(len(result.usable)))
    info = {
        "pdf_pages": len(texts),
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "source_sha256": hashlib.sha256(eprint).hexdigest(),
        "main_tex": result.main,
        "equations": len(result.equations),
        "unconvertible": len(result.bad),
        "usable_pages": len(result.usable),
        "macro_failures": result.macro_failures[:5],
        "anchors": result.anchors,
    }
    return result, pages, info


def cmd_build(args: argparse.Namespace) -> int:
    order = json.loads(args.order.read_text(encoding="utf-8"))
    pool = {c["id"]: c for c in json.loads((args.root / "pool.json").read_text())["candidates"]}
    seed = order["seed"]
    accepted: list[dict] = []
    log: list[dict] = []
    held = args.root / "decisions.json"
    done = {e["id"]: e for e in json.loads(held.read_text())} if held.is_file() else {}
    with httpx.Client(headers={"User-Agent": ac.USER_AGENT}) as client:
        for arxiv_id in order["order"]:
            if len([e for e in log if e["outcome"] == "accepted"]) >= args.target:
                break
            record = pool[arxiv_id]
            entry = {
                "id": arxiv_id,
                "style": record["style"],
                "set": record["set_spec"],
                "licence": record["licence_name"],
            }
            try:
                analysis, pages, info = process(record, args.root, client, args.pause, seed)
            except Skip as exc:
                entry |= {"outcome": "skipped", "reason": exc.code, "detail": exc.detail}
            except Exception as exc:  # a source that breaks the reader is a skip, with its type
                entry |= {"outcome": "skipped", "reason": "reader_error", "detail": repr(exc)[:200]}
            else:
                entry |= {"outcome": "accepted", "pages": [p + 1 for p in pages]} | info
                accepted.append(
                    {"record": record, "analysis": analysis, "pages": pages, "info": info}
                )
            log.append(entry)
            done[arxiv_id] = entry
            print(json.dumps(entry)[:240], flush=True)
            held.write_text(json.dumps(list(done.values()), indent=1), encoding="utf-8")
    REPO_DATA.mkdir(parents=True, exist_ok=True)
    (REPO_DATA / "candidates.json").write_text(
        json.dumps(
            {"seed": seed, "order_sha256": sha256_text(args.order), "candidates": log}, indent=1
        )
        + "\n",
        encoding="utf-8",
    )
    if len(accepted) < args.target:
        print(f"only {len(accepted)} accepted of {args.target}", file=sys.stderr)
    emit(accepted, args, seed)
    return 0


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_papers(accepted: list[dict], seed: str, n_held: int) -> dict[str, list[dict]]:
    """Held-out papers: from each style, in proportion, the first by the seeded hash."""
    by_style: dict[str, list[dict]] = {}
    for entry in accepted:
        by_style.setdefault(entry["record"]["style"], []).append(entry)
    total = len(accepted)
    held: list[dict] = []
    remaining = n_held
    styles = sorted(by_style, key=lambda s: -len(by_style[s]))
    for i, style in enumerate(styles):
        share = remaining if i == len(styles) - 1 else round(n_held * len(by_style[style]) / total)
        share = min(share, len(by_style[style]), remaining)
        ranked = sorted(
            by_style[style], key=lambda e: ac.stable_key(seed, "split", e["record"]["id"])
        )
        held += ranked[:share]
        remaining -= share
    held_ids = {e["record"]["id"] for e in held}
    return {
        "heldout": sorted(held, key=lambda e: e["record"]["id"]),
        "dev": sorted(
            (e for e in accepted if e["record"]["id"] not in held_ids),
            key=lambda e: e["record"]["id"],
        ),
    }


def emit(accepted: list[dict], args: argparse.Namespace, seed: str) -> None:
    parts = split_papers(accepted, seed, args.held_out)
    for part, entries in parts.items():
        out = args.root / "gold" / part
        if out.exists():
            for old in list(out.rglob("*")):
                if old.is_file():
                    old.unlink()
        (out / "pdfs").mkdir(parents=True, exist_ok=True)
        manifest: dict = {"part": part, "seed": seed, "papers": []}
        for entry in entries:
            record, analysis, pages, info = (
                entry["record"],
                entry["analysis"],
                entry["pages"],
                entry["info"],
            )
            slug = ag.paper_slug(record["id"])
            pdf_rel = f"pdfs/{slug}.pdf"
            ag.cut_pdf(args.root / "papers" / slug / "paper.pdf", pages, out / pdf_rel)
            rows = ag.gold_records(
                analysis,
                pages,
                record,
                pdf_relpath=pdf_rel,
                retrieved_at=args.date,
                pdf_sha256=info["pdf_sha256"],
                source_sha256=info["source_sha256"],
            )
            gold_path = out / f"{slug}.jsonl"
            gold_path.write_text(
                "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                encoding="utf-8",
            )
            manifest["papers"].append(
                {
                    "id": record["id"],
                    "title": record["title"],
                    "licence": record["licence_name"],
                    "licence_url": record["license"],
                    "set": record["set_spec"],
                    "style": record["style"],
                    "pages_chosen": [p + 1 for p in pages],
                    "equations": len(rows),
                    "display": sum(1 for r in rows if r["display"] == "block"),
                    "pdf_sha256": info["pdf_sha256"],
                    "source_sha256": info["source_sha256"],
                    "extract_pdf_sha256": ag.sha256_file(out / pdf_rel),
                    "gold_sha256": ag.sha256_file(gold_path),
                }
            )
        manifest["n_papers"] = len(entries)
        manifest["n_equations"] = sum(p["equations"] for p in manifest["papers"])
        manifest["n_pages"] = sum(len(p["pages_chosen"]) for p in manifest["papers"])
        text = json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
        (out / "manifest.json").write_text(text, encoding="utf-8")
        (REPO_DATA / f"manifest-{part}.json").write_text(text, encoding="utf-8")
        print(
            part,
            manifest["n_papers"],
            "papers",
            manifest["n_pages"],
            "pages",
            manifest["n_equations"],
            "equations",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--order", type=Path, required=True)
    b.add_argument("--target", type=int, default=40)
    b.add_argument("--held-out", type=int, default=15)
    b.add_argument("--pause", type=float, default=3.0)
    b.add_argument("--date", default="2026-10-21")
    b.set_defaults(func=cmd_build)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
