"""Collect every OpenStax book and section seen anywhere in development.

Sources:
  1. cached archive JSON (any directory named openstax, or any *.json that looks like an
     OpenStax archive page or book payload) under the roots below;
  2. section-slug-like strings (N-M-words) in text files, with the file they came from;
  3. file and directory names with section slugs;
  4. listings of .tgz/.tar.gz archives under the roots.

Prints a summary only (book slug, section slug, where seen). No page content.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import tarfile
from pathlib import Path

ROOTS = [Path(p) for p in sys.argv[1:]] or [
    Path(p) for p in os.environ.get("A11Y_SCAN_ROOTS", "").split(os.pathsep) if p
] or [Path(__file__).resolve().parents[2], Path("/tmp")]
SLUG = re.compile(r"(?<![0-9A-Za-z-])([0-9]{1,2}-[0-9]{1,2}-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)")
TEXT_EXT = {".py", ".md", ".json", ".jsonl", ".txt", ".csv", ".toml", ".yml", ".yaml",
            ".html", ".log", ".tsv", ".sh", ""}
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "axe-core", ".mypy_cache",
             ".ruff_cache", ".pytest_cache", "site-packages"}
MAX_TEXT = 30_000_000

books: dict[str, dict] = {}  # uuid -> {slug,title,license}
page_hits: dict[str, set] = collections.defaultdict(set)  # page uuid -> where
page_slugs: dict[str, str] = {}  # page uuid -> slug
slug_hits: dict[str, set] = collections.defaultdict(set)  # slug -> top-level source labels
book_pages: dict[str, dict[str, str]] = {}  # book slug -> page uuid -> page slug


def label(path: Path) -> str:
    s = str(path)
    for root in ROOTS:
        r = str(root)
        if s.startswith(r):
            rest = s[len(r):].lstrip("/").split("/")
            return r + "/" + "/".join(rest[:3])
    return s


def walk_tree(node: dict, book_slug: str) -> None:
    kids = node.get("contents")
    if kids:
        for k in kids:
            walk_tree(k, book_slug)
        return
    uuid = str(node.get("id", "")).split("@", 1)[0]
    book_pages.setdefault(book_slug, {})[uuid] = node.get("slug", "")


def scan_json(path: Path) -> bool:
    """True when the file is an OpenStax archive book or page payload."""
    try:
        if path.stat().st_size > 60_000_000:
            return
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if "tree" in data and "license" in data and "id" in data:
        uuid = str(data["id"]).split("@", 1)[0]
        books[uuid] = {
            "slug": data.get("slug"),
            "title": data.get("title"),
            "license": (data.get("license") or {}).get("url"),
        }
        walk_tree(data["tree"], data.get("slug") or uuid)
        return True
    if "content" in data and "id" in data and "slug" in data and "abstract" in data:
        uuid = str(data["id"]).split("@", 1)[0]
        page_slugs[uuid] = data["slug"]
        page_hits[uuid].add(label(path))
        return True
    return False


def scan_text(path: Path) -> None:
    try:
        if path.stat().st_size > MAX_TEXT:
            return
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return
    for m in SLUG.findall(text):
        slug_hits[m].add(label(path))


def scan_tar(path: Path) -> None:
    try:
        with tarfile.open(path) as tf:
            for member in tf.getmembers():
                for m in SLUG.findall(member.name):
                    slug_hits[m].add(label(path) + " [tar]")
                if member.isfile() and member.size < 5_000_000 and Path(member.name).suffix in TEXT_EXT - {""}:
                    f = tf.extractfile(member)
                    if f is None:
                        continue
                    text = f.read().decode("utf-8", errors="ignore")
                    if member.name.endswith(".json") and '"tree"' in text[:200000] and '"license"' in text[:200000]:
                        continue
                    if member.name.endswith(".json") and '"abstract"' in text and '"content"' in text:
                        continue
                    for m in SLUG.findall(text):
                        slug_hits[m].add(label(path) + " [tar:text]")
    except Exception as exc:
        print(f"# tar read failed: {path}: {type(exc).__name__}", file=sys.stderr)


for root in ROOTS:
    if not root.exists():
        continue
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dp = Path(dirpath)
        for m in SLUG.findall(dp.name):
            slug_hits[m].add(label(dp) + " [dirname]")
        for name in filenames:
            p = dp / name
            for m in SLUG.findall(name):
                slug_hits[m].add(label(p) + " [filename]")
            suf = p.suffix.lower()
            if suf == ".json" and scan_json(p):
                continue
            if name.endswith((".tgz", ".tar.gz")):
                scan_tar(p)
                continue
            if suf in TEXT_EXT and suf != "" :
                scan_text(p)

# map cached pages to books
uuid_to_book = {}
for bslug, pages in book_pages.items():
    for puuid in pages:
        uuid_to_book.setdefault(puuid, set()).add(bslug)

print("== cached books ==")
for uuid, b in sorted(books.items(), key=lambda kv: str(kv[1]["slug"])):
    print(f"{b['slug']}\t{b['license']}")
print()
print("== cached pages (book, slug, where) ==")
for puuid, slug in sorted(page_slugs.items(), key=lambda kv: kv[1]):
    bs = ",".join(sorted(uuid_to_book.get(puuid, {"?"})))
    print(f"{bs}\t{slug}\t{'; '.join(sorted(page_hits[puuid]))[:300]}")
print()
print("== slugs in text / names (slug, n sources, sources) ==")
known = {}
for bslug, pages in book_pages.items():
    for puuid, s in pages.items():
        known.setdefault(s, set()).add(bslug)
for slug, where in sorted(slug_hits.items()):
    bks = ",".join(sorted(known.get(slug, {"?"})))
    print(f"{slug}\t[{bks}]\t{len(where)}\t{'; '.join(sorted(where))[:400]}")
