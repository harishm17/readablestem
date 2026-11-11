"""What a model was asked, as a key.

A recorded answer is good for one question and no other. `asked` lists everything that
decides the answer to a request, in the terms the provider receives it:

- the step, the model and the prompt version (named, so a miss can say which changed);
- the system text as sent (the instructions, with the untrusted-text notice when the system
  block carries it);
- each picture by what it shows (`image_identity`), not by how a PNG encoder wrote it;
- the document text as sent: defanged, cut to the request's limit and fenced
  (`base.sent_context`), so a change to the fence or the limit is a new question;
- the token limit and the image detail;
- the thinking effort the client will ask for, and whether a refusal may be answered by
  another model (Claude only);
- the answer format: the JSON schema of the type the reply is parsed into, whose field
  descriptions the model reads.

`question_key` is the SHA-256 of that list. Nothing about the release is in it: a new
pipeline version, a changed routing rule or a new check after the answer leave every key as
it was, and an edit to a prompt, a schema, the fence or a crop changes the keys of exactly
the requests it touches. The same key names an answer in a recorded answers file
(`eval.answers`) and a page-level answer kept for a job's next try (`pipeline.run`).

`difference` says in content-free words why a request has no recorded answer, from the
lists stored beside the recorded ones ("q" on each row).

Two older keys are kept. `request_key` is the key of the sealed proof bundle
(`proof.replay`), whose recorded answers are committed; it does not change. `legacy_key` is
what answers files used before `KEY_VERSION` 2; `eval.answers.CachedClient` reads it only
when asked to re-key a file.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Iterable, Mapping
from functools import cache
from typing import Any, Final

from pydantic import BaseModel

from readablestem.config import Settings
from readablestem.core.images import open_within_budget
from readablestem.models.base import VisionRequest, sent_context, sent_system

KEY_VERSION: Final = 2
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
IDENTITY_KEYWORD: Final = "a11y-proof-identity"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- images


def declared_identity(png: bytes) -> str | None:
    """The identity a stand-in PNG declares in its text chunk, if it has one."""
    if png[:8] != PNG_SIGNATURE:
        return None
    pos = 8
    while pos + 8 <= len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind = png[pos + 4 : pos + 8]
        if kind == b"tEXt":
            key, _, value = png[pos + 8 : pos + 8 + length].partition(b"\x00")
            if key == IDENTITY_KEYWORD.encode("ascii"):
                return value.decode("ascii", "replace")
        if kind in (b"IDAT", b"IEND"):
            return None
        pos += 12 + length
    return None


def pixel_identity(png: bytes) -> str:
    """SHA-256 of the decoded pixels ("px:..."), or of the bytes ("raw:...") when the
    data is not an image PIL can read."""
    try:
        with open_within_budget(png) as image:
            rgba = image.convert("RGBA")
    except (OSError, ValueError, SyntaxError):
        return "raw:" + hashlib.sha256(png).hexdigest()
    digest = hashlib.sha256(f"{rgba.width}x{rgba.height}".encode())
    digest.update(rgba.tobytes())
    return "px:" + digest.hexdigest()


def image_identity(png: bytes) -> str:
    return declared_identity(png) or pixel_identity(png)


# ----------------------------------------------------------------------------- keys


def effective_effort(settings: Settings, request: VisionRequest) -> str | None:
    """The thinking effort a Claude client will send for `request` (None: not Claude)."""
    if settings.model_provider != "anthropic":
        return None
    return request.effort or settings.anthropic_effort_for(request.model)


def _notice_in_system(settings: Settings) -> bool:
    return settings.model_provider == "anthropic" and bool(
        getattr(settings, "anthropic_notice_in_system", False)
    )


def _refusal_fallback(settings: Settings, model: str) -> bool:
    """Whether a refusal by `model` may be answered by another model (Claude only)."""
    if settings.model_provider != "anthropic" or not getattr(
        settings, "anthropic_refusal_fallback", False
    ):
        return False
    from readablestem.models.anthropic_client import FALLBACK_MODELS

    return model in FALLBACK_MODELS


@cache
def schema_hash(schema: type[BaseModel]) -> str:
    """The answer format as the model is given it: field names, types and descriptions."""
    return _sha(json.dumps(schema.model_json_schema(), sort_keys=True, separators=(",", ":")))


def asked(settings: Settings, request: VisionRequest, schema: type[BaseModel]) -> dict[str, Any]:
    """Everything that decides the answer to `request` (module docstring). Content-free:
    document text and instructions appear only as hashes."""
    notice = _notice_in_system(settings)
    context = sent_context(request.context_text, request.context_limit, notice_in_system=notice)
    return {
        "v": KEY_VERSION,
        "step": request.step,
        "model": request.model,
        "prompt": request.prompt_version,
        "system": _sha(sent_system(request.instructions, notice_in_system=notice)),
        "images": [image_identity(png) for png in request.images],
        "context": None if context is None else _sha(context),
        "max_output_tokens": request.max_output_tokens,
        "image_detail": request.image_detail,
        "effort": effective_effort(settings, request),
        "fallback": _refusal_fallback(settings, request.model),
        "schema": schema_hash(schema),
    }


def key_of(parts: Mapping[str, Any]) -> str:
    return _sha(json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def question_key(settings: Settings, request: VisionRequest, schema: type[BaseModel]) -> str:
    """The key of the answer to `request`: equal keys mean the same question."""
    return key_of(asked(settings, request, schema))


def request_key(request: VisionRequest) -> str:
    """The key of the sealed proof bundle's recorded answers (unchanged since they were made)."""
    parts = [
        request.step,
        request.model,
        request.prompt_version,
        _sha(request.instructions),
        [image_identity(png) for png in request.images],
        request.context_text,
        request.max_output_tokens,
        request.image_detail,
    ]
    return _sha(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


def legacy_key(settings: Settings, request: VisionRequest) -> str:
    """The key answers files used before `KEY_VERSION` 2 (no schema, fence or fallback)."""
    extra = [
        effective_effort(settings, request),
        bool(getattr(settings, "anthropic_notice_in_system", False)),
    ]
    return _sha(request_key(request) + json.dumps(extra, separators=(",", ":")))


# --------------------------------------------------------------------------- misses

# What each part is called when it is the reason for a miss, in the order they are named.
_PART_NAMES: Final = {
    "model": "model",
    "prompt": "prompt",
    "system": "instructions",
    "schema": "answer format",
    "context": "nearby text",
    "effort": "effort",
    "max_output_tokens": "token limit",
    "image_detail": "image detail",
    "fallback": "refusal fallback",
    "v": "key version",
}
# Parts short and content-free enough to print both values of.
_PRINTED: Final = frozenset(
    {"model", "prompt", "effort", "max_output_tokens", "image_detail", "fallback", "v"}
)


def _differing(parts: Mapping[str, Any], other: Mapping[str, Any]) -> list[str]:
    names = [name for name in _PART_NAMES if parts.get(name) != other.get(name)]
    if "prompt" in names and "system" in names:
        names.remove("system")  # a new prompt version is a new text: say it once
    return names


def difference(parts: Mapping[str, Any], recorded: Iterable[Mapping[str, Any]]) -> str:
    """Why the question `parts` has no answer among the `recorded` questions, as a short
    content-free phrase: what differs from the recorded question nearest to it."""
    same_step = [r for r in recorded if r.get("step") == parts["step"]]
    if not same_step:
        return "no answer recorded for this step"
    same_pictures = [r for r in same_step if r.get("images") == parts["images"]]
    if not same_pictures:
        return "no answer recorded for this picture"
    nearest = min(same_pictures, key=lambda r: len(_differing(parts, r)))
    reasons = []
    for name in _differing(parts, nearest):
        label = _PART_NAMES[name]
        if name in _PRINTED:
            reasons.append(f"{label} {nearest.get(name)} recorded, {parts.get(name)} asked")
        else:
            reasons.append(f"{label} changed")
    return "; ".join(reasons) or "recorded under another key"
