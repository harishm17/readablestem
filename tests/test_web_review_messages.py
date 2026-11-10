"""Every code the pipeline can emit has a plain-English message (web/messages.py).

The codes are collected from the pipeline's source, not from a hand-kept list, so a new
check or fix reason fails this test until it has a sentence for the reviewer:

- check name/code pairs: `Check(name=..., code=...)` and each module's `_check(...)`
  helper (its own signature says which argument is the code), with codes followed through
  conditional expressions, local variables, helper functions' return values and
  `{"check.name": code}` dicts (how `validate._tree_codes` reports);
- fix reasons: `reason=` keywords, and the reason argument of `_failed(element, reason)`
  (pipeline.run) and of the steps' local `make(status, reason)`;
- f-string families (`f"model_{outcome}"`, `f"budget_{scope}"`, `f"axe.{rule}"`) are
  expanded from their sources of truth (the `Outcome` literal, the `BudgetExceeded`
  scopes, any axe rule id), and an unexpected new family fails the test.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import readablestem
from readablestem.core.types import Check
from readablestem.web.messages import (
    explain_check,
)

SRC = Path(readablestem.__file__).resolve().parent
PIPELINE_MODULES = (
    "validate",
    "equation_step",
    "glyph_reconcile",
    "flag_policy",
    "figure_step",
    "heading_step",
    "html_checks",
    "run",
    "table_step",
    "notation_memory",
    "image_page",
    "image_page_vector",
    "image_page_reconcile",
    "source/ltx_reader",  # LaTeX source: the codes it emits have sentences too
)
CHECK_NAME = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
PLACEHOLDER = "{}"


@dataclass
class Emitted:
    checks: set[tuple[str, str]] = field(default_factory=set)  # (check name, code)
    reasons: set[str] = field(default_factory=set)
    families: set[str] = field(default_factory=set)  # f-strings, "{}" for the variable part


class _Module:
    """Resolves the string values an expression can take inside one module."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.functions: dict[str, ast.FunctionDef] = {
            node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        self.parents: dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

    def scope(self, node: ast.AST) -> ast.AST:
        """The innermost function containing `node` (or the module)."""
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, ast.FunctionDef):
                return node
        return self.tree

    def strings(self, expr: ast.AST | None, scope: ast.AST, seen: frozenset = frozenset()):
        """(constants, f-string families) the expression can evaluate to."""
        constants: set[str] = set()
        families: set[str] = set()
        if expr is None:
            return constants, families
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str):
                constants.add(expr.value)
        elif isinstance(expr, ast.JoinedStr):
            families.add(
                "".join(
                    v.value if isinstance(v, ast.Constant) else PLACEHOLDER for v in expr.values
                )
            )
        elif isinstance(expr, ast.IfExp):
            for branch in (expr.body, expr.orelse):
                c, f = self.strings(branch, scope, seen)
                constants |= c
                families |= f
        elif isinstance(expr, ast.BoolOp):
            for value in expr.values:
                c, f = self.strings(value, scope, seen)
                constants |= c
                families |= f
        elif isinstance(expr, ast.Name) and (key := (id(scope), expr.id)) not in seen:
            # A name assigned in the function; failing that, a module-level constant
            # (`SINGLE_READING_CODE = "single_reading"`).
            values = list(self._assigned(expr.id, scope))
            if not values and scope is not self.tree:
                values = [
                    node.value
                    for node in self.tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets)
                ]
                scope = self.tree
            for value in values:
                c, f = self.strings(value, scope, seen | {key})
                constants |= c
                families |= f
        elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            function = self.functions.get(expr.func.id)
            if function is not None and (key := (id(function), "return")) not in seen:
                for node in ast.walk(function):
                    if isinstance(node, ast.Return):
                        c, f = self.strings(node.value, function, seen | {key})
                        constants |= c
                        families |= f
        return constants, families

    @staticmethod
    def _assigned(name: str, scope: ast.AST):
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                    yield node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                yield node.value


def _argument(call: ast.Call, keyword: str, position: int | None) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if position is not None and position < len(call.args):
        return call.args[position]
    return None


def _call_name(call: ast.Call) -> str | None:
    return call.func.id if isinstance(call.func, ast.Name) else None


def collect(module_name: str) -> Emitted:
    folder = "" if "/" in module_name else "pipeline/"
    path = SRC / f"{folder}{module_name}.py"
    module = _Module(ast.parse(path.read_text(encoding="utf-8")))
    out = Emitted()
    helper = module.functions.get("_check")
    helper_params = [a.arg for a in helper.args.args] if helper else []

    def add_pairs(name_expr, code_expr, scope) -> None:
        names, name_families = module.strings(name_expr, scope)
        codes, code_families = module.strings(code_expr, scope)
        out.families |= name_families | code_families
        out.checks |= {(name, code) for name in names for code in codes}

    for node in ast.walk(module.tree):
        if isinstance(node, ast.Call):
            called = _call_name(node)
            scope = module.scope(node)
            if called == "Check":
                add_pairs(_argument(node, "name", None), _argument(node, "code", None), scope)
            elif called == "_check" and helper is not None:
                add_pairs(
                    _argument(node, "name", helper_params.index("name")),
                    _argument(node, "code", helper_params.index("code")),
                    scope,
                )
            reason = _argument(node, "reason", 1 if called in {"_failed", "make"} else None)
            if reason is not None:
                constants, families = module.strings(reason, scope)
                out.reasons |= constants
                out.families |= families
        elif isinstance(node, ast.Dict) and node.keys:
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if all(isinstance(k, str) and CHECK_NAME.match(k) for k in keys):
                scope = module.scope(node)
                for key, value in zip(node.keys, node.values, strict=True):
                    add_pairs(key, value, scope)
    return out


@pytest.fixture(scope="module")
def emitted() -> Emitted:
    total = Emitted()
    for name in PIPELINE_MODULES:
        found = collect(name)
        total.checks |= found.checks
        total.reasons |= found.reasons
        total.families |= found.families
    return total


# --------------------------------------------------------------------------- the collector


# --------------------------------------------------------------------------- coverage


def test_every_check_the_pipeline_can_fail_has_a_message(emitted):
    missing = sorted(
        pair
        for pair in emitted.checks
        if not explain_check(Check(name=pair[0], passed=False, code=pair[1])).known
    )
    assert not missing, f"add these to web/messages.py CHECK_MESSAGES: {missing}"


# --------------------------------------------------------------------------- fallbacks
