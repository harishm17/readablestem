"""The conversion pipeline end to end (WP4 implements).

convert_pdf: layout.analyze -> heading_fixes -> (equations, figures in a thread pool,
each crop -> step) -> assemble.render_html -> html_checks.static_checks. A BudgetExceeded
stops further model calls: remaining fixes become FAILED with reason "budget_<scope>" and
the document is still assembled with originals kept.
"""

from __future__ import annotations

from collections.abc import Callable

from a11y_stem.config import Settings
from a11y_stem.core.types import ConversionOutput
from a11y_stem.models.base import VisionClient
from a11y_stem.observability.tracing import Tracer

PIPELINE_VERSION = "0.2.0"

ProgressFn = Callable[[int, int], None]  # (pages_done, pages_total)


def convert_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: ProgressFn | None = None,
    tracer: Tracer | None = None,
    concurrency: int = 4,
) -> ConversionOutput:
    raise NotImplementedError("WP4")
