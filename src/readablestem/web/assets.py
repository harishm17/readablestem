"""Static files: names that carry their content's hash, and cache lifetimes to match.

`static_url("app.css")` is `/static/app.<12 hex of the file's SHA-256>.css`; the static
route serves that name from `app.css` with `Cache-Control: public, max-age=31536000,
immutable`, so a browser fetches our stylesheet and script once and never asks again until
the file changes (and then the name does). Pages are `no-store`, so a changed hash reaches
every visitor with the next page.

Other rules, for every file under `/static/`:

- `vendor/` files carry their version in the name (`htmx-2.0.11.min.js`,
  `fonts/latinmodern-math-1.959.woff2`; `vendor/README.md` says to rename on update), so
  they are immutable too.
- Anything else (the demo's files, an unhashed `app.css` that an old page or the error
  pages ask for) is revalidated every time with `Cache-Control: no-cache` and the ETag
  Starlette already sends: a 304 with no body when nothing changed.
- A hashed name whose hash is not the file's today is a 404, not the new file under an
  old name: an immutable URL never changes what it serves.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = Path(__file__).resolve().parent / "static"
HASH_LENGTH = 12
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
_HASHED = re.compile(rf"^(?P<stem>.+)\.(?P<hash>[0-9a-f]{{{HASH_LENGTH}}})(?P<ext>\.[A-Za-z0-9]+)$")

# path -> ((mtime_ns, size), hash): a file is read again only when it changed.
_seen: dict[str, tuple[tuple[int, int], str]] = {}


def content_hash(name: str, root: Path = STATIC_DIR) -> str | None:
    """The first `HASH_LENGTH` hex digits of the SHA-256 of `root/name`; None when there
    is no such file (or the name leaves `root`)."""
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    known = _seen.get(str(path))
    if known is not None and known[0] == key:
        return known[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:HASH_LENGTH]
    _seen[str(path)] = (key, digest)
    return digest


def static_url(name: str) -> str:
    """The URL of a static file, with its hash in the name (`/static/app.1a2b3c4d5e6f.css`);
    the plain URL when the file is missing (the 404 is then the page's to show)."""
    digest = content_hash(name)
    if digest is None:
        return f"/static/{name}"
    stem, dot, ext = name.rpartition(".")
    return f"/static/{stem}.{digest}{dot}{ext}" if dot else f"/static/{name}.{digest}"


def unhashed(path: str) -> str | None:
    """`app.css` for `app.1a2b3c4d5e6f.css` when that hash is the file's; else None."""
    match = _HASHED.match(path)
    if match is None:
        return None
    real = match["stem"] + match["ext"]
    return real if content_hash(real) == match["hash"] else None


class CachedStaticFiles(StaticFiles):
    """`StaticFiles` that serves hashed names and sets the cache headers (module docstring)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        real = unhashed(path)
        response = await super().get_response(real or path, scope)
        if response.status_code in (200, 304):
            immutable = real is not None or path.startswith("vendor/")
            response.headers["Cache-Control"] = IMMUTABLE if immutable else REVALIDATE
        return response


def clear_hashes() -> None:
    """Forget the hashes read so far (tests)."""
    _seen.clear()
