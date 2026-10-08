"""Every source file must work on every Python that pyproject claims (>=3.12).

CI runs the suite on 3.12 and 3.14, but a fault that only newer interpreters reject should
be caught on 3.12 as well, before anything is pushed. The one seen so far: a string with
lone surrogate escapes (`"\\ud835\\udc63"`, a UTF-16 pair written as two `\\u` escapes).
In Python those are two lone surrogates, not the character U+1D463, and from 3.13 on a
docstring containing them no longer compiles (docstring dedenting encodes to UTF-8), so
the whole module fails to import. Write the character itself or `\\U0001d463`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(
    p for folder in ("src", "tests", "migrations") for p in (ROOT / folder).rglob("*.py")
)

# Known offenders in files another work stream owns right now; each must be fixed there.
# Non-strict, so the fix landing makes this an XPASS rather than a failure.
PENDING = {
    "src/a11y_stem/pipeline/parse.py": "surrogate escapes in is_math_alphanumeric's "
    "docstring (layout-v2 stream owns parse.py)",
}


def _params():
    for path in SOURCES:
        rel = path.relative_to(ROOT).as_posix()
        marks = [pytest.mark.xfail(reason=PENDING[rel], strict=False)] if rel in PENDING else []
        yield pytest.param(path, id=rel, marks=marks)


def bad_string_lines(source: str) -> list[int]:
    """Lines of string literals that cannot be encoded as UTF-8 (lone surrogates)."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node.value.encode("utf-8")
            except UnicodeEncodeError:
                lines.append(node.lineno)
    return lines


def test_the_check_catches_surrogate_escapes():
    assert bad_string_lines('def f():\n    """italic \\ud835\\udc63"""\n') == [2]
    assert bad_string_lines(f'x = "\\U0001d463"\ny = "{chr(0x1D463)}"\n') == []


@pytest.mark.parametrize("path", list(_params()))
def test_string_literals_are_valid_utf8(path: Path):
    bad = bad_string_lines(path.read_text(encoding="utf-8"))
    assert not bad, f"lone surrogate escapes on lines {bad}; write the character itself"


def test_pending_entries_still_exist():
    for rel in PENDING:
        assert (ROOT / rel).is_file(), rel
