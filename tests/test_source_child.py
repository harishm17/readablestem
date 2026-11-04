"""A LaTeX source converted in the real conversion child (`web.isolated_convert`), the way the
worker does it: the child holds no key and no database, its address space and CPU time are
capped, and LaTeXML runs in the sandbox below it. Skipped without LaTeXML and bubblewrap."""

from __future__ import annotations

import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models import make_vision_client
from readablestem.source import envelope, latexml
from readablestem.web import isolated_convert
from readablestem.web.isolated_convert import ConversionFailed, convert_isolated
from readablestem.web.worker import error_code_for
from source_support import fixture, make_zip, needs_latexml

pytestmark = needs_latexml


@pytest.fixture(autouse=True)
def dev_root_reaches_the_child(monkeypatch):
    """Only where LaTeXML is unpacked under a development root: production has it in /usr."""
    monkeypatch.setattr(
        isolated_convert, "_EXTRA_ENV", (*isolated_convert._EXTRA_ENV, latexml.DEV_ROOT_VAR)
    )


def offline():
    settings = Settings(_env_file=None, env="dev", fake_models=True)  # type: ignore[call-arg]
    budget = JobBudget(guard=InMemorySpendGuard(100.0), max_usd=50.0, max_calls=1000)
    return settings, make_vision_client(settings, budget, fake=True)


def test_a_source_converts_in_the_child_and_the_hook_finds_it():
    settings, client = offline()
    blob = envelope.wrap("tex", "a.tex", None, fixture("amsmath.tex").encode())
    output = convert_isolated(blob, settings=settings, client=client, limit_s=120)
    assert output.result.origin == "latex"
    assert output.result.stats.required_decisions == 0
    assert "<math" in output.result.html


def test_a_refused_source_ends_the_child_with_a_plain_code_the_worker_stores():
    settings, client = offline()
    source = "\\documentclass{article}\\begin{document}\\immediate\\write18{id}\\end{document}"
    blob = envelope.wrap("zip", "p.zip", "main.tex", make_zip({"main.tex": source}))
    with pytest.raises(ConversionFailed) as raised:
        convert_isolated(blob, settings=settings, client=client, limit_s=120)
    assert raised.value.code == "source_refused"
    assert error_code_for(raised.value) == "source_refused"
    assert "write18" not in str(raised.value)
