"""The bytes a LaTeX source job carries to the conversion child.

A job's upload is stored as the file the person sent (`Blob` kind "upload", media type
`application/x-tex` or `application/zip`). The worker hands the converter one `bytes` value, as
it does for a PDF; for a source it is this envelope: a magic line, one JSON line with the kind
(`tex` or `zip`), the main file the person named (or null) and the display name, then the
upload as it was. `pipeline.run.convert_pdf` recognises the magic line and passes the envelope
to `source.convert`. Kept in a module of its own so that hook imports nothing heavy.
"""

from __future__ import annotations

import json

MAGIC = b"RSSRC1\n"


class EnvelopeError(ValueError):
    pass


def wrap(kind: str, filename: str, main: str | None, data: bytes) -> bytes:
    head = json.dumps({"kind": kind, "main": main, "name": filename}, ensure_ascii=True)
    return MAGIC + head.encode("ascii") + b"\n" + data


def is_envelope(data: bytes) -> bool:
    return data.startswith(MAGIC)


def unwrap(blob: bytes) -> tuple[str, str | None, bytes]:
    """(kind, main or None, the upload bytes); `EnvelopeError` for anything else."""
    if not is_envelope(blob):
        raise EnvelopeError("not an envelope")
    head, _, data = blob[len(MAGIC) :].partition(b"\n")
    try:
        meta = json.loads(head)
        kind = meta["kind"]
        main = meta.get("main")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise EnvelopeError("bad header") from None
    if kind not in ("tex", "zip") or not (main is None or isinstance(main, str)):
        raise EnvelopeError("bad header")
    return kind, main, data
