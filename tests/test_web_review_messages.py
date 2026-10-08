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
from typing import get_args

import pytest

import a11y_stem
from a11y_stem.core.types import Check, Outcome
from a11y_stem.web import messages
from a11y_stem.web.messages import (
    CHECK_MESSAGES,
    REASON_MESSAGES,
    confidence_label,
    explain_check,
    explain_reason,
)

SRC = Path(a11y_stem.__file__).resolve().parent
PIPELINE_MODULES = (
    "validate",
    "equation_step",
    "figure_step",
    "heading_step",
    "html_checks",
    "run",
)
CHECK_NAME = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
PLACEHOLDER = "{}"
# Dynamic families and where their values come from (see the tests below).
KNOWN_FAMILIES = {"model_{}", "budget_{}", "axe.{}"}


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
            for value in self._assigned(expr.id, scope):
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
    path = SRC / "pipeline" / f"{module_name}.py"
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


def budget_scopes() -> set[str]:
    """Every BudgetExceeded scope: the literal first argument wherever one is raised
    (core.budget, web.spend), the worker's cancellation scope, and the orchestrator's
    fallback when no scope was recorded."""
    from a11y_stem.web.worker import JobCancelled

    pattern = re.compile(r'BudgetExceeded\(\s*"([a-z_]+)"')
    scopes = {m for path in SRC.rglob("*.py") for m in pattern.findall(path.read_text())}
    return scopes | {JobCancelled().scope, "unknown"}


# --------------------------------------------------------------------------- the collector


def test_the_collector_finds_codes_from_every_kind_of_source(emitted):
    expected = {
        ("latex.allowed_commands", "unknown_command"),  # validate._check(name, code)
        ("latex.balanced_environments", "unbalanced_env"),  # through a local variable
        ("mathml.root", "nested_math"),  # through the _tree_codes dict
        ("mathml.safe_attributes", "unknown_attribute"),  # through a helper's return
        ("speech.generated", "speech_empty"),  # Check(code=code) with code assigned
        ("equation.text_layer_style", "style_mismatch"),  # Check(code=... if ... else None)
        ("heading.evidence", "heading.weak_evidence"),  # heading_step._check(name, ok, code)
        ("html.heading_order", "heading_skip"),  # html_checks._check(name, ok, code)
        ("figure.decorative_consistency", "decorative_conflict"),
        ("alt.length", "too_long"),
    }
    assert expected <= emitted.checks
    assert {"low_confidence", "invalid_latex", "invalid_alt", "not_equation"} <= emitted.reasons
    assert {"internal_error", "crop_error", "heading.empty_text"} <= emitted.reasons
    assert len(emitted.checks) >= 55


def test_no_unexpected_dynamic_code_families(emitted):
    assert emitted.families == KNOWN_FAMILIES


# --------------------------------------------------------------------------- coverage


def test_every_check_the_pipeline_can_fail_has_a_message(emitted):
    missing = sorted(
        pair
        for pair in emitted.checks
        if not explain_check(Check(name=pair[0], passed=False, code=pair[1])).known
    )
    assert not missing, f"add these to web/messages.py CHECK_MESSAGES: {missing}"


def test_every_fix_reason_has_a_message(emitted):
    reasons = set(emitted.reasons)
    reasons |= {f"model_{outcome}" for outcome in get_args(Outcome)}
    reasons |= {f"budget_{scope}" for scope in budget_scopes()}
    missing = sorted(r for r in reasons if not explain_reason(r).known)
    assert not missing, f"add these to web/messages.py REASON_MESSAGES: {missing}"


def test_axe_rules_get_a_message_naming_the_rule():
    explained = explain_check(Check(name="axe.color-contrast", passed=False, code="color-contrast"))
    assert explained.known
    assert explained.text == (
        "The automatic accessibility check (axe-core) reported a problem with its rule "
        '"color-contrast".'
    )


def test_no_message_for_a_code_the_pipeline_no_longer_emits(emitted):
    """Stale entries mislead whoever reads the table; remove them with the code."""
    reasons = set(emitted.reasons)
    reasons |= {f"model_{outcome}" for outcome in get_args(Outcome)}
    reasons |= {f"budget_{scope}" for scope in budget_scopes()}
    assert sorted(set(CHECK_MESSAGES) - emitted.checks) == []
    assert sorted(set(REASON_MESSAGES) - reasons) == []


# A full stop, question or exclamation mark followed by a capital starts a second sentence.
SENTENCE_BREAK = re.compile(r"[.!?]\s+[A-Z]")


def test_messages_are_one_plain_sentence_each():
    texts = [
        *CHECK_MESSAGES.values(),
        *REASON_MESSAGES.values(),
        messages.UNKNOWN_CHECK_MESSAGE,
        messages.UNKNOWN_REASON_MESSAGE,
        messages.AXE_RULE_MESSAGE.format(rule="color-contrast"),
    ]
    for text in texts:
        assert text[0].isupper() and text.endswith("."), text
        assert not SENTENCE_BREAK.search(text), f"more than one sentence: {text}"
        assert len(text) <= 220, text
        assert "compl" + "iant" not in text.lower()
        assert "_" not in text, f"looks like a code, not a sentence: {text}"


# --------------------------------------------------------------------------- fallbacks


def test_unknown_codes_fall_back_to_a_neutral_message_with_the_code():
    explained = explain_check(Check(name="latex.something_new", passed=False, code="new_code"))
    assert not explained.known
    assert explained.text == messages.UNKNOWN_CHECK_MESSAGE
    assert explained.code == "latex.something_new: new_code"
    reason = explain_reason("brand_new_reason")
    assert (reason.known, reason.text, reason.code) == (
        False,
        messages.UNKNOWN_REASON_MESSAGE,
        "brand_new_reason",
    )
    no_code = explain_check(Check(name="mathml.well_formed", passed=False))
    assert no_code.code == "mathml.well_formed" and not no_code.known


def test_the_same_code_on_two_checks_gets_two_sentences():
    latex = explain_check(Check(name="latex.not_empty", passed=False, code="empty"))
    alt = explain_check(Check(name="alt.not_empty", passed=False, code="empty"))
    assert "transcription" in latex.text and "alt text" in alt.text


def test_severity_is_said_in_words():
    error = Check(name="alt.length", passed=False, code="too_long", severity="error")
    warning = error.model_copy(update={"severity": "warning"})
    assert messages.severity_label(error) == "Must be fixed"
    assert messages.severity_label(warning) == "Worth checking"


@pytest.mark.parametrize(
    ("confidence", "label"),
    [
        (None, "Not given"),
        (0.0, "Low (0%)"),
        (0.45, "Low (45%)"),
        (0.594, "Low (59%)"),
        (0.595, "Medium (60%)"),  # the word follows the number shown
        (0.82, "Medium (82%)"),
        (0.85, "High (85%)"),
        (1.0, "High (100%)"),
    ],
)
def test_confidence_label(confidence, label):
    assert confidence_label(confidence) == label
