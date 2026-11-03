"""Reader B, local adapter (models/math_readers/local_reader.py and local_weights.py).

Offline. The decode loop and the weights step run against stand-ins; the tests that need the
real model files skip unless `LOCAL_READER_TEST_DIR` points at them (see
docs/math-readers.md: `python -m readablestem.models.math_readers.local_weights --dir DIR`)."""

from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models.math_readers import ReadRequest, build_readers, local_weights
from readablestem.models.math_readers.local_reader import (
    LocalReader,
    LocalReaderError,
    _Model,
    preprocess,
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


def test_preprocess_gives_the_models_input_shape_and_range():
    pixels = preprocess(png())
    assert pixels.shape == (1, 1, 448, 448) and pixels.dtype == np.float32
    # White paper is (1 - mean) / std above the mean; ink is far below it; padding is 0.
    assert pixels.max() < 1.0 and pixels.min() < -5.0
    assert pixels[0, 0, -1, -1] == 0.0


def test_preprocess_trims_the_white_border_and_scales_the_short_side():
    pixels = preprocess(png(400, 200, ink_box=(150, 80, 250, 120)))[0, 0]
    rows = np.nonzero(np.any(pixels != 0.0, axis=1))[0]
    cols = np.nonzero(np.any(pixels != 0.0, axis=0))[0]
    # The 101 x 41 ink box fills the canvas to 448 wide (the long side) and 182 high.
    assert cols.max() + 1 == 448 and 175 <= rows.max() + 1 <= 185


def test_preprocess_treats_transparent_pixels_as_white():
    pixels = preprocess(png(mode="RGBA"))
    assert pixels.shape == (1, 1, 448, 448) and pixels.min() < -5.0


def test_preprocess_of_a_blank_crop_does_not_fail():
    blank = io.BytesIO()
    Image.new("RGB", (80, 30), "white").save(blank, "PNG")
    assert preprocess(blank.getvalue()).shape == (1, 1, 448, 448)


def test_preprocess_refuses_what_is_not_an_image():
    with pytest.raises(ValueError):
        preprocess(b"\x89PNG\r\n\x1a\n" + b"junk")


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


def test_decode_runs_the_prefill_once_then_steps_with_the_cache_until_the_end_token():
    model, prefill, step = stand_in_model([7, 8, 9, 2])  # x { } <eos>
    text = reader()._decode(model, preprocess(png()), deadline=1e12)
    assert text == "x{}"
    assert len(prefill.fed) == 1 and set(prefill.fed[0]) == {"input_ids", "encoder_hidden_states"}
    assert prefill.fed[0]["input_ids"].tolist() == [[0]]  # the start token
    assert len(step.fed) == 3
    # each step gets the last token only, and the whole cache (24 decoder + 24 encoder arrays)
    assert step.fed[0]["input_ids"].tolist() == [[7]]
    assert sum(k.startswith("past_key_values.") for k in step.fed[0]) == 48
    # the encoder's keys and values are the ones made by the prefill, kept as they were
    assert step.fed[2]["past_key_values.0.encoder.key"].shape[2] == 785
    assert step.fed[2]["past_key_values.0.decoder.key"].shape[2] == 2


def test_a_decode_past_its_time_budget_is_none():
    model, *_ = stand_in_model([7, 8, 2])
    assert reader()._decode(model, preprocess(png()), deadline=0.0) is None


def test_a_decode_without_an_end_token_stops_at_the_token_limit():
    model, prefill, step = stand_in_model([7] * 10)
    text = reader(max_tokens=4)._decode(model, preprocess(png()), deadline=1e12)
    assert text is not None and len(prefill.fed) + len(step.fed) == 4


def read_with(model: _Model, request: ReadRequest, **kw: Any):
    r = reader(**kw)
    r._loaded = model
    return r.read(request)


def test_read_returns_one_candidate_without_confidence_and_costs_nothing():
    model, *_ = stand_in_model([7, 2])
    result = read_with(model, ReadRequest(png()))
    assert [c.latex for c in result.candidates] == ["x"]
    assert result.candidates[0].confidence is None
    call = result.call
    assert (call.reader, call.model, call.outcome) == ("local", "texteller", "ok")
    assert call.cost_usd == 0.0 and call.sends == 0 and call.latency_ms >= 0


def test_read_of_an_empty_decode_is_invalid_output():
    model, *_ = stand_in_model([2])
    result = read_with(model, ReadRequest(png()))
    assert result.candidates == [] and result.call.outcome == "invalid_output"


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


def test_missing_model_files_are_a_setup_error_not_a_bad_crop(tmp_path):
    r = LocalReader(tmp_path)
    assert not r.available()
    with pytest.raises(LocalReaderError):
        r.read(ReadRequest(png()))


# ------------------------------------------------------------------ settings and factory


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def budget() -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=1)


def test_it_is_off_by_default_and_builds_nothing():
    s = settings()
    assert s.math_reader_local_enabled is False and s.math_reader_local_dir is None
    assert build_readers(s, budget()) == {}


def test_enabled_without_a_directory_is_a_config_error():
    with pytest.raises(ConfigError, match="MATH_READER_LOCAL_DIR"):
        build_readers(settings(math_reader_local_enabled=True), budget())


def test_enabled_builds_a_reader_without_loading_the_model(tmp_path):
    s = settings(
        math_reader_local_enabled=True,
        math_reader_local_dir=tmp_path,
        math_reader_local_threads=3,
        math_reader_local_time_s=7,
    )
    built = build_readers(s, budget())
    local = built["local"]
    assert isinstance(local, LocalReader) and local.name == "local"
    assert local._loaded is None and local._threads == 3 and local._budget_s == 7


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


def test_the_pins_are_complete_and_at_one_revision():
    assert [s.name for s in local_weights.SOURCES] == list(local_weights.FILES)
    for s in local_weights.SOURCES:
        assert re.fullmatch(r"[0-9a-f]{64}", s.sha256) and s.size > 0
    assert re.fullmatch(r"[0-9a-f]{40}", local_weights.REVISION)
    assert local_weights.BASE_URL.startswith("https://huggingface.co/OleehyO/TexTeller/resolve/")
    assert local_weights.REVISION in local_weights.BASE_URL  # a revision, never "main"


def test_prepare_downloads_checks_and_quantizes_the_onnx_files(tmp_path, pinned_to_fakes):
    seen: list[str] = []
    out = local_weights.prepare(tmp_path / "m", opener=opener(seen), quantizer=copy_quantizer)
    assert sorted(u.rsplit("/", 1)[1] for u in seen) == sorted(local_weights.FILES)
    for name in local_weights.ONNX_FILES:
        assert (out / name).read_bytes() == b"int8:" + FAKE[name]
    for name in (local_weights.VOCAB_FILE, local_weights.ADDED_TOKENS_FILE):
        assert (out / name).read_bytes() == FAKE[name]  # not quantized
    assert local_weights.REVISION in (out / local_weights.MARKER).read_text()
    assert (out / "decoder_model.onnx").stat().st_mode & 0o777 == 0o644
    assert [p.name for p in out.iterdir() if p.name.startswith(".")] == []  # no leftovers
    assert LocalReader(out).available()


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


def test_main_reports_an_error_with_a_nonzero_exit(tmp_path, capsys):
    assert local_weights.main(["--dir", str(tmp_path / "m"), "--from", str(tmp_path / "none")]) == 1
    assert "error:" in capsys.readouterr().err


def test_the_weights_module_needs_only_the_standard_library():
    """The Docker build runs it alone, in a stage with no application installed."""
    source = (ROOT / "src/readablestem/models/math_readers/local_weights.py").read_text()
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.M))
    assert not {i for i in imports if i.startswith(("readablestem", "numpy", "httpx", "PIL"))}


# ------------------------------------------------------------------ the image


def test_the_dockerfile_builds_the_weights_only_when_asked():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "ARG INSTALL_MATH_READER=false" in dockerfile
    assert "local_weights.py" in dockerfile and "MATH_READER_LOCAL_DIR=" in dockerfile
    env = (ROOT / ".env.example").read_text()
    assert "MATH_READER_LOCAL_ENABLED=false" in env


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


@needs_weights
def test_the_real_model_respects_a_tiny_time_budget():
    local = LocalReader(Path(MODEL_DIR), threads=1, time_budget_s=0.05)
    result = local.read(ReadRequest(rendered("x+2y=3")))
    assert result.call.outcome == "timeout" and result.candidates == []
