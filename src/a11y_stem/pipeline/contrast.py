"""Colour maths for coloured text in the output (FEATURE_COLOUR): contrast and lightness.

Coloured text keeps its colour in the output (`Element.colour_runs`), but the output's
text must reach 4.5:1 contrast on its background (WCAG 1.4.3). A colour that does not is
made darker, keeping its hue: the colour is moved to OKLCH (OKLab's lightness, chroma
and hue; Ottosson 2020), its lightness lowered until the contrast is reached, and its
chroma lowered only as far as needed to stay inside sRGB. So light grey stays grey, red
stays red and yellow becomes a dark yellow, not brown or black.

Contrast is WCAG's: relative luminance from linearised sRGB, ratio (L1 + 0.05) / (L2 +
0.05). Pure functions; colours are (r, g, b) tuples of 0-255 integers.
"""

from __future__ import annotations

import math

RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)
# The output's background (`assemble.STYLE`: body background #ffffff). The output has no
# dark mode (`color-scheme: light`), so there is one background to reach contrast on.
OUTPUT_BACKGROUND: RGB = WHITE
MIN_TEXT_CONTRAST = 4.5  # WCAG 1.4.3 (AA), normal-size text

_STEPS = 32  # bisection steps: far finer than one 8-bit step of a channel
_NEUTRAL_CHROMA = 0.002  # OKLCH chroma below this is rounding noise on a grey


def _linear(channel: float) -> float:
    """sRGB channel in [0, 1] -> linear light."""
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _encoded(linear: float) -> float:
    """Linear light -> sRGB channel in [0, 1] (not clamped)."""
    if linear <= 0.0031308:
        return 12.92 * linear
    return 1.055 * math.copysign(abs(linear) ** (1 / 2.4), linear) - 0.055


def relative_luminance(rgb: RGB) -> float:
    r, g, b = (_linear(c / 255) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """WCAG contrast ratio of two colours, 1.0 to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def oklch(rgb: RGB) -> tuple[float, float, float]:
    """(lightness 0-1, chroma, hue in degrees) of an sRGB colour."""
    r, g, b = (_linear(c / 255) for c in rgb)
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _srgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    """OKLCH -> sRGB channels in [0, 1], possibly out of gamut (not clamped)."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long, medium, short = l_**3, m_**3, s_**3  # LMS cone responses
    return (
        _encoded(4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short),
        _encoded(-1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short),
        _encoded(-0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short),
    )


def _in_gamut(channels: tuple[float, float, float]) -> bool:
    return all(-1e-6 <= c <= 1 + 1e-6 for c in channels)


def _quantised(channels: tuple[float, float, float]) -> RGB:
    r, g, b = (min(255, max(0, round(c * 255))) for c in channels)
    return r, g, b


def _at_lightness(lightness: float, chroma: float, hue: float) -> RGB:
    """The colour at this lightness and hue, with as much of `chroma` as sRGB can show."""
    if _in_gamut(_srgb(lightness, chroma, hue)):
        return _quantised(_srgb(lightness, chroma, hue))
    low, high = 0.0, chroma
    for _ in range(_STEPS):
        mid = (low + high) / 2
        if _in_gamut(_srgb(lightness, mid, hue)):
            low = mid
        else:
            high = mid
    return _quantised(_srgb(lightness, low, hue))


def with_contrast(
    rgb: RGB, background: RGB = OUTPUT_BACKGROUND, minimum: float = MIN_TEXT_CONTRAST
) -> RGB:
    """`rgb` itself when it reaches `minimum` contrast on `background`, else the lightest
    colour of the same hue that does (darker than `rgb`; the background is light)."""
    if contrast_ratio(rgb, background) >= minimum:
        return rgb
    lightness, chroma, hue = oklch(rgb)
    if chroma < _NEUTRAL_CHROMA:
        chroma = 0.0  # a grey has no hue to keep: stay exactly neutral
    best: RGB = (0, 0, 0)
    low, high = 0.0, lightness
    for _ in range(_STEPS):
        mid = (low + high) / 2
        candidate = _at_lightness(mid, chroma, hue)
        if contrast_ratio(candidate, background) >= minimum:
            best, low = candidate, mid
        else:
            high = mid
    return best


def hex_colour(rgb: RGB) -> str:
    """ "#rrggbb"."""
    return "#{:02x}{:02x}{:02x}".format(*rgb)
