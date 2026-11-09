from __future__ import annotations

from typing import Any

import pytest

from aimrg.packs.expr import (
    MAX_EXPRESSION_LENGTH,
    VARIABLES,
    ExpressionError,
    compile_expression,
)

CONTEXT: dict[str, Any] = {
    "track": "model",
    "tier": 2,
    "ai_category": "ml_non_generative",
    "uses": ["credit_decision", "pricing"],
    "consequential_decision": True,
    "third_party": False,
    "jurisdictions": ["US", "US-CO"],
    "stage": "validation",
}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("true", True),
        ("false", False),
        ('track == "model"', True),
        ('track != "model"', False),
        ("tier <= 2", True),
        ("tier < 2", False),
        ("1 <= tier <= 3", True),
        ("tier > 3 or tier == 2", True),
        ('"credit_decision" in uses', True),
        ('"marketing" not in uses', True),
        ("not third_party", True),
        ('consequential_decision and "US-CO" in jurisdictions', True),
        ('ai_category in ["generative", "agentic"]', False),
        ('track == "model" and tier <= 2 and not third_party', True),
    ],
)
def test_evaluate(source: str, expected: bool) -> None:
    assert compile_expression(source).evaluate(CONTEXT) is expected


def test_records_referenced_variables() -> None:
    expr = compile_expression('track == "model" and tier <= 2')
    assert expr.variables == {"track", "tier"}
    assert "track" in repr(expr)


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('echo pwned')",
        "uses.append('x')",
        "uses[0] == 'x'",
        "len(uses) > 0",
        "[x for x in uses]",
        "lambda: 1",
        "tier + 1 > 2",
        "tier is 1",
        "{'a': 1}",
        "None",
        "[tier]",
    ],
)
def test_rejects_unsafe_or_unsupported_syntax(source: str) -> None:
    with pytest.raises(ExpressionError):
        compile_expression(source)


def test_rejects_unknown_variables() -> None:
    with pytest.raises(ExpressionError, match="unknown variable"):
        compile_expression("risk_level > 3")


def test_rejects_syntax_errors() -> None:
    with pytest.raises(ExpressionError, match="syntax error"):
        compile_expression("tier <=")


def test_rejects_overlong_expressions() -> None:
    with pytest.raises(ExpressionError, match="longer"):
        compile_expression("true or " * MAX_EXPRESSION_LENGTH + "true")


def test_missing_context_variable() -> None:
    with pytest.raises(ExpressionError, match="missing"):
        compile_expression("tier <= 2").evaluate({"track": "model"})


def test_type_error_is_wrapped() -> None:
    with pytest.raises(ExpressionError, match="type error"):
        compile_expression('tier < "high"').evaluate(CONTEXT)


def test_every_documented_variable_is_usable() -> None:
    for name in VARIABLES:
        compile_expression(f"{name} == {name}").evaluate(CONTEXT)
