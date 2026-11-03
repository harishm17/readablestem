"""Reader B on this machine: TexTeller (Apache-2.0), run on CPU with ONNX Runtime.

No network at runtime and nothing leaves the process, so this reader is not a recipient of
anything and needs no key. The crop is read by a 300M-parameter image-to-LaTeX model whose
files sit in `Settings.math_reader_local_dir` (fetched at image build, pinned by SHA-256:
`models.math_readers.local_weights`). It is built to differ from the page pass's reading in
the way that matters most: a different model family with no shared prompt, so a wrong reading
is wrong in a different way.

Cost and limits (measured on the development box, `docs/math-readers.md`): the int8 files
are about 0.6 GB and about 1 GB resident once loaded (lazily, on the first read, so a process
that never reads pays nothing); a read takes about 1 s on two threads and 2 s on one. `read`
has its own time budget (`Settings.math_reader_local_time_s`): a read that runs out returns no
candidates with the outcome `timeout`. The decode is greedy, one beam, at most 256 tokens.

Confidence: the reader reports none (`None`); the model's token probabilities are not a
usable confidence and are not made into one.

Nothing from the crop or the LaTeX is logged.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image, UnidentifiedImageError

from readablestem.config import ConfigError, Settings
from readablestem.models.math_readers.base import (
    Candidate,
    ReaderCall,
    ReadRequest,
    ReadResult,
    clean_latex,
)
from readablestem.models.math_readers.local_weights import (
    ADDED_TOKENS_FILE,
    ENCODER_FILE,
    FILES,
    PREFILL_FILE,
    STEP_FILE,
    VOCAB_FILE,
)
from readablestem.observability.logging import log_fields

log = logging.getLogger(__name__)

MODEL_ID: Final = "texteller"

# Preprocessing constants of the model (texteller.constants): grayscale, mean and std of
# formula images, a 448-pixel square canvas.
_IMAGE_MEAN: Final = 0.9545467
_IMAGE_STD: Final = 0.15394445
_SIZE: Final = 448
_TRIM_THRESHOLD: Final = 15
_BOS, _PAD, _EOS = 0, 1, 2
_SPECIAL_IDS: Final = frozenset({0, 1, 2, 3, 4})  # <s> <pad> </s> <unk> <mask>
_LAYERS: Final = 12
_HEADS: Final = 16
_HEAD_DIM: Final = 64
_MAX_TOKENS: Final = 256  # a handwritten expression is far shorter; bounds a runaway decode


@dataclass(frozen=True)
class _Model:
    encoder: Any
    prefill: Any
    step: Any
    tokens: dict[int, str]
    literal: set[int]


class LocalReaderError(ConfigError):
    """The model files are missing or unusable (a configuration problem, not a bad crop)."""


def _byte_decoder() -> dict[str, int]:
    """GPT-2 byte-level BPE: the printable character that stands for each byte."""
    keep = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    chars = keep[:]
    extra = 0
    for byte in range(256):
        if byte not in keep:
            keep.append(byte)
            chars.append(256 + extra)
            extra += 1
    return {chr(c): b for b, c in zip(keep, chars, strict=True)}


def preprocess(png: bytes) -> np.ndarray:
    """The model's input from a PNG crop: trimmed of white border, scaled so the longer side
    is at most 448 (bicubic), grayscale, normalised, padded to 448 x 448 on the right and
    bottom. Shape (1, 1, 448, 448), float32. Raises `ValueError` for an unreadable image."""
    try:
        with Image.open(io.BytesIO(png)) as opened:
            rgba = opened.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("image could not be read") from exc
    # Transparent pixels become white, as on paper.
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    rgb = np.asarray(Image.alpha_composite(white, rgba).convert("RGB"))
    h, w = rgb.shape[:2]
    corners = [tuple(rgb[0, 0]), tuple(rgb[0, -1]), tuple(rgb[-1, 0]), tuple(rgb[-1, -1])]
    background = max(set(corners), key=corners.count)
    diff = np.abs(rgb.astype(np.int16) - np.array(background, dtype=np.int16)).max(axis=2)
    ys, xs = np.nonzero(diff > _TRIM_THRESHOLD)
    if ys.size:
        rgb = rgb[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        h, w = rgb.shape[:2]
    gray = Image.fromarray(rgb).convert("L")
    # torchvision's Resize(447, max_size=448): the shorter side to 447, unless that would
    # make the longer one more than 448.
    short, long_ = (h, w) if h <= w else (w, h)
    scale_short = (_SIZE - 1) / short
    if long_ * scale_short > _SIZE:
        new_long = _SIZE
        new_short = max(1, int(_SIZE * short / long_))
    else:
        new_short, new_long = _SIZE - 1, int(long_ * scale_short)
    new_h, new_w = (new_short, new_long) if h <= w else (new_long, new_short)
    gray = gray.resize((max(1, new_w), max(1, new_h)), Image.Resampling.BICUBIC)
    pixels = (np.asarray(gray, dtype=np.float32) / 255.0 - _IMAGE_MEAN) / _IMAGE_STD
    canvas = np.zeros((_SIZE, _SIZE), dtype=np.float32)  # padding is after normalising: 0
    canvas[: pixels.shape[0], : pixels.shape[1]] = pixels
    return canvas[None, None]


class LocalReader:
    """Reader B on this machine. `read` is safe to call from several threads: one decode at
    a time (the weights are shared and the threads are capped)."""

    name = "local"
    model = MODEL_ID

    def __init__(
        self,
        model_dir: Path,
        *,
        threads: int = 2,
        time_budget_s: float = 60.0,
        max_tokens: int = _MAX_TOKENS,
    ) -> None:
        self._dir = Path(model_dir)
        self._threads = max(1, threads)
        self._budget_s = time_budget_s
        self._max_tokens = max_tokens
        self._lock = threading.Lock()
        self._loaded: _Model | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalReader:
        if settings.math_reader_local_dir is None:
            raise LocalReaderError("MATH_READER_LOCAL_DIR is not set")
        return cls(
            settings.math_reader_local_dir,
            threads=settings.math_reader_local_threads,
            time_budget_s=settings.math_reader_local_time_s,
        )

    # ------------------------------------------------------------------ loading

    def available(self) -> bool:
        """True when every model file is in place (nothing is loaded)."""
        return all((self._dir / f).is_file() for f in FILES)

    def self_check(self) -> bool:
        """Load the model and run the encoder on a blank crop: True when the files work."""
        blank = np.zeros((1, 1, _SIZE, _SIZE), dtype=np.float32)
        shape = self._load().encoder.run(None, {"pixel_values": blank})[0].shape
        return tuple(shape) == (1, 785, 768)

    def _load(self) -> _Model:
        if self._loaded is not None:
            return self._loaded
        if not self.available():
            raise LocalReaderError("model files are missing")
        import onnxruntime as ort  # heavy: only when a read is made

        options = ort.SessionOptions()
        options.intra_op_num_threads = self._threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False  # hand memory back instead of pooling it
        options.log_severity_level = 3

        def session(name: str) -> Any:
            return ort.InferenceSession(
                str(self._dir / name), options, providers=["CPUExecutionProvider"]
            )

        vocab = json.loads((self._dir / VOCAB_FILE).read_text(encoding="utf-8"))
        added = json.loads((self._dir / ADDED_TOKENS_FILE).read_text(encoding="utf-8"))
        tokens = {int(i): str(t) for t, i in vocab.items()}
        literal = {int(i) for i in added.values()}
        tokens.update({int(i): str(t) for t, i in added.items()})
        self._loaded = _Model(
            session(ENCODER_FILE), session(PREFILL_FILE), session(STEP_FILE), tokens, literal
        )
        return self._loaded

    # ------------------------------------------------------------------ reading

    def read(self, request: ReadRequest) -> ReadResult:
        """Never raises for a crop it cannot use: an empty result and an outcome code. Raises
        `LocalReaderError` when the model files are missing (a setup problem)."""
        started = time.monotonic()
        deadline = started + self._budget_s
        try:
            pixels = preprocess(request.image_png)
        except ValueError:
            return self._result([], "invalid_output", started)
        with self._lock:
            text = self._decode(self._load(), pixels, deadline)
        if text is None:
            return self._result([], "timeout", started)
        latex = clean_latex(text)
        if latex is None:
            return self._result([], "invalid_output", started)
        return self._result([Candidate(latex)], "ok", started)

    def _decode(self, model: _Model, pixels: np.ndarray, deadline: float) -> str | None:
        """Greedy decode. None when the time budget ran out; a runaway decode (no end token
        within `max_tokens`) is returned as it stands and is judged by the caller."""
        hidden = model.encoder.run(None, {"pixel_values": pixels})[0]
        ids = [_BOS]
        past: dict[str, np.ndarray] = {}
        for step in range(self._max_tokens):
            if time.monotonic() > deadline:
                return None
            if step == 0:  # the start token alone: also makes the encoder's keys and values
                run = model.prefill
                feed: dict[str, Any] = {
                    "input_ids": np.array([ids], dtype=np.int64),
                    "encoder_hidden_states": hidden,
                }
            else:
                run = model.step
                feed = {"input_ids": np.array([ids[-1:]], dtype=np.int64), **past}
            names = [o.name for o in run.get_outputs()]
            outputs = dict(zip(names, run.run(None, feed), strict=True))
            nxt = int(np.argmax(outputs["logits"][0, -1]))
            if nxt == _EOS:
                break
            ids.append(nxt)
            for key, value in outputs.items():
                if key.startswith("present."):
                    past["past_key_values." + key[len("present.") :]] = value
                # The step model returns only the decoder's own keys and values; the
                # encoder's, made once by the first step, stay in `past`.
        return self._text(ids[1:], model.tokens, model.literal)

    @staticmethod
    def _text(ids: list[int], tokens: dict[int, str], literal: set[int]) -> str:
        decoder = _BYTE_DECODER
        out = bytearray()
        for i in ids:
            if i in _SPECIAL_IDS or i not in tokens:
                continue
            piece = tokens[i]
            if i in literal:
                out += piece.encode("utf-8")
            else:
                out += bytes(decoder.get(ch, 63) for ch in piece)
        return out.decode("utf-8", errors="replace").strip()

    def _result(self, candidates: list[Candidate], outcome: Any, started: float) -> ReadResult:
        millis = int((time.monotonic() - started) * 1000)
        log_fields(
            log,
            logging.INFO,
            "math_reader_local",
            outcome=outcome,
            latency_ms=millis,
            candidates=len(candidates),
        )
        call = ReaderCall(
            reader=self.name,
            model=self.model,
            outcome=outcome,
            cost_usd=0.0,
            latency_ms=millis,
            sends=0,
        )
        return ReadResult(candidates, call)


_BYTE_DECODER: Final = _byte_decoder()
