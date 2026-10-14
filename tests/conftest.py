"""Test-wide set-up: process-wide state that one test must not leave for the next (the
model rate gates, and what the worker has seen of the model provider)."""

from __future__ import annotations

import pytest

from readablestem.models import base
from readablestem.web import provider_health


@pytest.fixture(autouse=True)
def _no_rate_pause_between_tests():
    """A 429 in one test pauses that provider's gate (`models.base.RateGate`); a pause
    must not make the next test wait."""
    base._GATES.clear()
    provider_health.HEALTH.reset()
    yield
    base._GATES.clear()
    provider_health.HEALTH.reset()
