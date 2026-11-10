"""Reader B, local adapter (models/math_readers/local_reader.py and local_weights.py).

Offline. The decode loop and the weights step run against stand-ins; the tests that need the
real model files skip unless `LOCAL_READER_TEST_DIR` points at them (see
docs/math-readers.md: `python -m readablestem.models.math_readers.local_weights --dir DIR`)."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models.math_readers import ReadRequest, local_weights
from readablestem.models.math_readers.local_reader import (
    LocalReader,
    _Model,
)
from readablestem.observability.logging import FIELDS_ATTR

ROOT = Path(__file__).resolve().parents[1]


def png(width: int = 200, height: int = 60, *, ink_box=(20, 10, 120, 40), mode="RGB") -> bytes:
    colour: Any = (255, 255, 255) if mode == "RGB" else (255, 255, 255, 0)
    image = Image.new(mode, (width, height), colour)
    ImageDraw.Draw(image).rectangle(ink_box, fill=(0, 0, 0) if mode == "RGB" else (0, 0, 0, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


# ------------------------------------------------------------------ preprocessing


# ------------------------------------------------------------------ token text

TOKENS = {5: "\\", 6: "frac", 7: "Ġx", 8: "{", 9: "}", 10: "ĠÃ©", 11: "一", 3: "<unk>"}


def test_tokens_are_joined_as_byte_level_pieces():
    text = LocalReader._text([5, 6, 8, 7, 9], TOKENS, set())
    assert text == "\\frac{ x}"
    # bytes C3 A9 written as two printable characters decode to one: é
    assert LocalReader._text([10], TOKENS, set()) == "é"
    # an added (literal) token is its own text, special ids and unknown ids are dropped
    assert LocalReader._text([11, 3, 0, 2, 999], TOKENS, {11}) == "一"


# ------------------------------------------------------------------ the decode loop


class Session:
    """A stand-in ONNX session: replays scripted token choices as one-hot logits and
    records what it was fed."""

    def __init__(self, script: list[int], outputs: list[str]) -> None:
        self.script = script
        self.fed: list[dict[str, Any]] = []
        self._outputs = outputs

    def get_outputs(self):
        return [type("O", (), {"name": n}) for n in self._outputs]

    def run(self, _names, feed):
        self.fed.append(feed)
        logits = np.zeros((1, 1, 15000), dtype=np.float32)
        logits[0, 0, self.script.pop(0)] = 1.0
        out = [logits]
        for name in self._outputs[1:]:
            length = len(self.fed) if name.endswith("decoder.key") else 785
            out.append(np.zeros((1, 16, length, 64), dtype=np.float32))
        return out


def stand_in_model(script: list[int]):
    prefill_out = ["logits"] + [
        f"present.{i}.{part}.{kv}"
        for i in range(12)
        for part in ("decoder", "encoder")
        for kv in ("key", "value")
    ]
    step_out = ["logits"] + [
        f"present.{i}.decoder.{kv}" for i in range(12) for kv in ("key", "value")
    ]
    encoder = type("E", (), {"run": lambda self, _n, _f: [np.zeros((1, 785, 768), np.float32)]})()
    prefill = Session(script[:1], prefill_out)
    step = Session(script[1:], step_out)
    return _Model(encoder, prefill, step, TOKENS, set()), prefill, step


def reader(**kw: Any) -> LocalReader:
    return LocalReader(Path("/nonexistent"), **kw)


def read_with(model: _Model, request: ReadRequest, **kw: Any):
    r = reader(**kw)
    r._loaded = model
    return r.read(request)


def test_read_past_its_budget_is_a_timeout_with_no_candidates():
    model, *_ = stand_in_model([7, 2])
    result = read_with(model, ReadRequest(png()), time_budget_s=-1.0)
    assert result.candidates == [] and result.call.outcome == "timeout"


def test_read_of_an_image_pillow_cannot_open_is_invalid_output_not_an_exception():
    model, *_ = stand_in_model([7, 2])
    result = read_with(model, ReadRequest(b"\x89PNG\r\n\x1a\n" + b"junk"))
    assert result.candidates == [] and result.call.outcome == "invalid_output"


def test_read_logs_codes_and_counts_never_the_latex(caplog):
    model, *_ = stand_in_model([5, 6, 2])  # \frac
    with caplog.at_level("INFO"):
        read_with(model, ReadRequest(png()))
    lines = [
        f"{r.getMessage()} {getattr(r, FIELDS_ATTR, {})}"
        for r in caplog.records
        if r.name.startswith("readablestem")
    ]
    text = "\n".join(lines)
    assert "math_reader_local" in text and "'outcome': 'ok'" in text
    assert "frac" not in text


# ------------------------------------------------------------------ settings and factory


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def budget() -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=1)


def test_it_is_not_a_recipient_of_anything(tmp_path):
    """Nothing leaves the process, so the privacy page and the production check must not
    change when it is on (unlike the Claude and OpenAI readers)."""
    from readablestem.web.recipients import recipients

    off = settings()
    on = settings(math_reader_local_enabled=True, math_reader_local_dir=tmp_path)
    assert on.math_readers_on == [] == off.math_readers_on
    assert recipients(on, tracer_on=False) == recipients(off, tracer_on=False)


# ------------------------------------------------------------------ weights: pins and checks

FAKE = {name: f"content of {name}".encode() for name in local_weights.FILES}


@pytest.fixture
def pinned_to_fakes(monkeypatch):
    monkeypatch.setattr(
        local_weights,
        "SOURCES",
        tuple(
            local_weights.Source(n, hashlib.sha256(b).hexdigest(), len(b)) for n, b in FAKE.items()
        ),
    )


class Reply(io.BytesIO):
    def __enter__(self):
        return self


def opener(seen: list[str], overrides: dict[str, bytes] | None = None):
    def open_(url: str):
        seen.append(url)
        name = url.rsplit("/", 1)[1]
        return Reply((overrides or {}).get(name, FAKE[name]))

    return open_


def copy_quantizer(source: Path, target: Path) -> None:
    target.write_bytes(b"int8:" + source.read_bytes())


def test_prepare_does_nothing_when_the_files_are_already_there(tmp_path, pinned_to_fakes):
    local_weights.prepare(tmp_path, opener=opener([]), quantizer=copy_quantizer)
    seen: list[str] = []
    local_weights.prepare(tmp_path, opener=opener(seen), quantizer=copy_quantizer)
    assert seen == []


def test_a_download_with_the_wrong_checksum_is_refused_and_nothing_is_installed(
    tmp_path, pinned_to_fakes
):
    bad = {local_weights.ENCODER_FILE: b"x" * len(FAKE[local_weights.ENCODER_FILE])}
    with pytest.raises(local_weights.WeightsError, match="checksum mismatch"):
        local_weights.prepare(tmp_path, opener=opener([], bad), quantizer=copy_quantizer)
    assert not (tmp_path / local_weights.MARKER).exists()
    assert not (tmp_path / local_weights.ENCODER_FILE).exists()


def test_a_download_larger_than_the_pin_is_refused(tmp_path, pinned_to_fakes):
    big = {local_weights.VOCAB_FILE: FAKE[local_weights.VOCAB_FILE] + b"more"}
    with pytest.raises(local_weights.WeightsError, match="larger"):
        local_weights.prepare(tmp_path, opener=opener([], big), quantizer=copy_quantizer)


def test_prepare_from_a_directory_checks_each_file(tmp_path, pinned_to_fakes):
    source = tmp_path / "src"
    source.mkdir()
    for name, body in FAKE.items():
        (source / name).write_bytes(body)
    local_weights.prepare(tmp_path / "ok", source_dir=source, int8=False)
    assert (tmp_path / "ok" / "decoder_model.onnx").read_bytes() == FAKE["decoder_model.onnx"]
    (source / "vocab.json").write_bytes(b"tampered..")
    with pytest.raises(local_weights.WeightsError):
        local_weights.prepare(tmp_path / "bad", source_dir=source, int8=False)


# ------------------------------------------------------------------ the image


# ------------------------------------------------ the real model (skipped without it)

MODEL_DIR = os.environ.get("LOCAL_READER_TEST_DIR", "")
needs_weights = pytest.mark.skipif(
    not MODEL_DIR or not LocalReader(Path(MODEL_DIR)).available(),
    reason="set LOCAL_READER_TEST_DIR to the local reader's model files",
)


def rendered(text: str) -> bytes:
    font = ImageFont.load_default(size=64)
    image = Image.new("RGB", (640, 140), "white")
    ImageDraw.Draw(image).text((30, 30), text, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@needs_weights
def test_the_real_model_loads_and_reads_a_rendered_expression():
    local = LocalReader(Path(MODEL_DIR), threads=2, time_budget_s=60)
    assert local.self_check()
    result = local.read(ReadRequest(rendered("x+2y=3")))
    assert result.call.outcome == "ok" and result.candidates
    latex = result.candidates[0].latex.replace(" ", "")
    assert "x" in latex and "2y" in latex and "3" in latex and "=" in latex
