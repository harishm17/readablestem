"""Synthetic scans: a born-digital PDF turned into the image-only PDF a scanner would make
of its printout, so the pages that have an answer key can test the image page pass (ADR 0007).

Each page is rendered at 300 dpi, then degraded the way a flatbed scanner or a phone camera
degrades a printout, and set in a new PDF as a picture of the whole page and nothing else (no
text layer, no hidden layer: `page_type` calls such a page `scan_no_text`):

- resampled to a lower resolution, 150 to 200 dpi, with a slightly soft filter;
- turned by a small angle, up to `MAX_ROTATION_DEG` either way (paper never sits square);
- uneven lighting: a gentle darkening towards one edge, and a faint paper tint;
- sensor grain: Gaussian noise;
- a blur of the optics (a radius under one pixel);
- JPEG compression at a quality of 55 to 75, the artefacts of a cheap scanner's output.

Everything that varies is drawn from a random generator seeded by the seed given and the
page's own pixels, so the same PDF and seed always give the same scan, byte for byte: the
requests a model run makes are the same on every replay (`eval/answers.py` keys on the image).
The measure of what degradation does to the pages is the evaluation's, not this module's:
the numbers in docs/eval-image-pages.md say how well the pass reads these pages, not how well
it reads every scan.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

SOURCE_DPI = 300
MAX_ROTATION_DEG = 1.5
MIN_OUT_DPI, MAX_OUT_DPI = 150, 200
JPEG_QUALITY = (55, 75)
NOISE_SIGMA = (4.0, 9.0)
BLUR_RADIUS = (0.5, 0.9)
# The darkening towards one edge, as a share of the brightness at the far side.
SHADING = (0.0, 0.10)
TINT = (0.0, 6.0)  # warm paper: levels taken off the blue channel


@dataclass(frozen=True)
class Degradation:
    """What was done to one page (content-free, for the report)."""

    dpi: int
    rotation_deg: float
    noise_sigma: float
    blur_radius: float
    jpeg_quality: int
    shading: float


def _rng(seed: int, pixels: bytes) -> np.random.Generator:
    digest = hashlib.sha256(seed.to_bytes(8, "big", signed=True) + pixels).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def degrade_image(source: Image.Image, seed: int) -> tuple[bytes, Degradation]:
    """The JPEG of one page after degradation, and what was done. `source` is the page at
    `SOURCE_DPI`, RGB."""
    rng = _rng(seed, source.resize((32, 32)).tobytes())
    dpi = int(rng.integers(MIN_OUT_DPI, MAX_OUT_DPI + 1))
    angle = float(rng.uniform(-MAX_ROTATION_DEG, MAX_ROTATION_DEG))
    sigma = float(rng.uniform(*NOISE_SIGMA))
    blur = float(rng.uniform(*BLUR_RADIUS))
    quality = int(rng.integers(JPEG_QUALITY[0], JPEG_QUALITY[1] + 1))
    shading = float(rng.uniform(*SHADING))
    tint = float(rng.uniform(*TINT))
    side = int(rng.integers(0, 4))  # which edge is darker

    scale = dpi / SOURCE_DPI
    size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    image = source.resize(size, Image.Resampling.BICUBIC)
    image = image.rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255)
    )
    image = image.filter(ImageFilter.GaussianBlur(blur))
    pixels = np.asarray(image, dtype=np.float32)
    height, width = pixels.shape[:2]
    ramp = np.linspace(1.0, 1.0 - shading, width if side < 2 else height, dtype=np.float32)
    if side in (1, 3):
        ramp = ramp[::-1]
    shade = ramp[None, :, None] if side < 2 else ramp[:, None, None]
    pixels = pixels * shade
    pixels[..., 2] -= tint
    pixels += rng.normal(0.0, sigma, size=pixels.shape).astype(np.float32)
    out = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")
    buffer = io.BytesIO()
    out.save(buffer, format="JPEG", quality=quality, subsampling=2, optimize=False)
    return buffer.getvalue(), Degradation(
        dpi, round(angle, 3), round(sigma, 2), round(blur, 2), quality, round(shading, 3)
    )


def scan_pdf(pdf: bytes, seed: int = 0) -> tuple[bytes, list[Degradation]]:
    """The image-only PDF of `pdf` as a scanner would have made it, and one `Degradation`
    per page. Page sizes are kept; nothing but a picture is on a page."""
    source = pymupdf.open(stream=pdf, filetype="pdf")
    target = pymupdf.open()
    made: list[Degradation] = []
    try:
        for page in source:
            pixmap = page.get_pixmap(dpi=SOURCE_DPI, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            jpeg, how = degrade_image(image, seed)
            sheet = target.new_page(width=page.rect.width, height=page.rect.height)
            sheet.insert_image(sheet.rect, stream=jpeg)
            made.append(how)
        return target.tobytes(deflate=True), made
    finally:
        target.close()
        source.close()
