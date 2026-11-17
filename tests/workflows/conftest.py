"""What every workflow test starts from: the app, signed in as the owner, converting from the
recording (`harness.ReplaySite`). One site per test file, so a file converts its document
once and each test reads one thing a person gets."""

from __future__ import annotations

from collections.abc import Iterator

import harness
import pytest


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[harness.ReplaySite]:
    site = harness.ReplaySite(tmp_path_factory.mktemp("site"))
    yield site
    site.close()
