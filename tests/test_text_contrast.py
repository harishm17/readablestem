"""pipeline.contrast: WCAG contrast and the colour adjustment for coloured text.

A colour that already reaches 4.5:1 on white is kept exactly; any other is made darker
until it does, keeping its hue (OKLCH), so red stays red and grey stays grey.
"""

from __future__ import annotations

import itertools

import pytest

from readablestem.pipeline.contrast import (
    MIN_TEXT_CONTRAST,
    WHITE,
    contrast_ratio,
    hex_colour,
    oklch,
    relative_luminance,
    with_contrast,
)


def test_contrast_matches_the_wcag_reference_values():
    assert contrast_ratio((0, 0, 0), WHITE) == pytest.approx(21.0)
    assert contrast_ratio(WHITE, WHITE) == pytest.approx(1.0)
    # Well-known values: #767676 is the lightest grey at 4.5:1, pure red is 4.0:1.
    assert contrast_ratio((0x76, 0x76, 0x76), WHITE) == pytest.approx(4.54, abs=0.01)
    assert contrast_ratio((255, 0, 0), WHITE) == pytest.approx(4.0, abs=0.01)
    assert contrast_ratio((0, 0, 255), WHITE) == pytest.approx(8.59, abs=0.01)
    assert contrast_ratio(WHITE, (0, 0, 0)) == contrast_ratio((0, 0, 0), WHITE)  # symmetric
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0)


def test_oklch_of_known_colours():
    lightness, chroma, _ = oklch((255, 255, 255))
    assert lightness == pytest.approx(1.0, abs=1e-3) and chroma < 1e-3
    lightness, chroma, hue = oklch((255, 0, 0))
    assert lightness == pytest.approx(0.628, abs=1e-3)
    assert chroma == pytest.approx(0.258, abs=1e-3)
    assert hue == pytest.approx(29.2, abs=0.1)


@pytest.mark.parametrize("rgb", [(0, 0, 0), (0, 0, 255), (0, 128, 0), (38, 38, 38), (51, 51, 179)])
def test_a_colour_that_is_dark_enough_is_kept_exactly(rgb):
    assert with_contrast(rgb) == rgb


@pytest.mark.parametrize(
    "rgb",
    [(255, 0, 0), (255, 255, 0), (255, 165, 0), (0, 255, 255), (210, 210, 210), (255, 192, 203)],
)
def test_a_light_colour_is_darkened_to_the_minimum_and_keeps_its_hue(rgb):
    adjusted = with_contrast(rgb)
    ratio = contrast_ratio(adjusted, WHITE)
    assert MIN_TEXT_CONTRAST <= ratio < MIN_TEXT_CONTRAST + 0.25  # no darker than needed
    before, after = oklch(rgb), oklch(adjusted)
    assert after[0] < before[0]
    if before[1] > 0.01:  # a colour, not a grey: the same hue
        assert abs((after[2] - before[2] + 180) % 360 - 180) < 3.0


def test_a_grey_stays_exactly_neutral():
    r, g, b = with_contrast((210, 210, 210))
    assert r == g == b
    assert hex_colour((r, g, b)) == "#767676"


def test_red_becomes_a_slightly_darker_red():
    assert hex_colour(with_contrast((255, 0, 0))) == "#ee0000"


def test_every_colour_on_a_coarse_grid_reaches_the_minimum():
    for rgb in itertools.product(range(0, 256, 51), repeat=3):
        assert contrast_ratio(with_contrast(rgb), WHITE) >= MIN_TEXT_CONTRAST, rgb


def test_hex_colour():
    assert hex_colour((0, 10, 255)) == "#000aff"
