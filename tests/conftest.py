"""Test-wide set-up: process-wide state that one test must not leave for the next."""

from __future__ import annotations

import pytest

from a11y_stem.models import base


@pytest.fixture(autouse=True)
def _no_rate_pause_between_tests():
    """A 429 in one test pauses that provider's gate (`models.base.RateGate`); a pause
    must not make the next test wait."""
    base._GATES.clear()
    yield
    base._GATES.clear()
