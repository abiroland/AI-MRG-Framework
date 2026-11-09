"""A small, safe expression language for control applicability (``applies_when``).

Expressions are parsed with Python's ``ast`` module and evaluated by walking a strict
whitelist of node types. Nothing is ever passed to ``eval``/``exec``: there are no
function calls, attribute access, subscripts, comprehensions or lambdas.

Grammar (informally)::

    expr     := or_expr
    or_expr  := and_expr ("or" and_expr)*
    and_expr := not_expr ("and" not_expr)*
    not_expr := "not" not_expr | compare
    compare  := operand (op operand)*      op: == != < <= > >= in "not in"
    operand  := VARIABLE | STRING | NUMBER | true | false | "[" literal, ... "]"

Examples::

    track == "model" and tier <= 2
    "credit_decision" in uses and not third_party
    true
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any

MAX_EXPRESSION_LENGTH = 500

#: Variables an expression may reference, with a short description of each.
VARIABLES: dict[str, str] = {
    "track": 'governance track: "model", "ai_system" or "non_model"',
    "tier": "effective risk tier, 1 (highest) to 4 (immaterial)",
    "ai_category": '"traditional", "ml_non_generative", "generative" or "agentic"',
    "uses": 'list of business-use tags, e.g. ["credit_decision"]',
    "consequential_decision": "true if the asset influences a consequential consumer decision",
    "third_party": "true if the asset is vendor or third-party provided",
    "jurisdictions": 'list of jurisdiction codes, e.g. ["US", "US-CO"]',
    "stage": "current lifecycle status of the asset",
}

_LITERAL_NAMES: dict[str, bool] = {"true": True, "false": False, "True": True, "False": False}

_COMPARATORS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class ExpressionError(ValueError):
    """Raised when an expression is invalid or cannot be evaluated."""


class Expression:
    """A parsed, validated applicability expression."""

    __slots__ = ("_tree", "source", "variables")

    def __init__(self, source: str) -> None:
        if len(source) > MAX_EXPRESSION_LENGTH:
            raise ExpressionError(f"expression longer than {MAX_EXPRESSION_LENGTH} characters")
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"syntax error in {source!r}: {exc.msg}") from exc
        names: set[str] = set()
        _validate(tree.body, names)
        unknown = names - VARIABLES.keys()
        if unknown:
            raise ExpressionError(
                f"unknown variable(s) {sorted(unknown)} in {source!r}; allowed: {sorted(VARIABLES)}"
            )
        self.source = source
        self.variables = frozenset(names)
        self._tree = tree

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        missing = self.variables - context.keys()
        if missing:
            raise ExpressionError(f"context is missing variable(s) {sorted(missing)}")
        try:
            return bool(_eval(self._tree.body, context))
        except TypeError as exc:
            raise ExpressionError(f"type error evaluating {self.source!r}: {exc}") from exc

    def __repr__(self) -> str:
        return f"Expression({self.source!r})"


def compile_expression(source: str) -> Expression:
    """Parse and validate ``source``; raises :class:`ExpressionError` if invalid."""
    return Expression(source)


def _validate(node: ast.AST, names: set[str]) -> None:
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _validate(value, names)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _validate(node.operand, names)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _COMPARATORS:
                raise ExpressionError(f"operator {type(op).__name__} is not allowed")
        _validate(node.left, names)
        for comparator in node.comparators:
            _validate(comparator, names)
    elif isinstance(node, ast.Name):
        if node.id not in _LITERAL_NAMES:
            names.add(node.id)
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, str | int | float | bool):
            raise ExpressionError(f"literal {node.value!r} is not allowed")
    elif isinstance(node, ast.List | ast.Tuple):
        for element in node.elts:
            if not isinstance(element, ast.Constant):
                raise ExpressionError("list elements must be literals")
            _validate(element, names)
    else:
        raise ExpressionError(f"{type(node).__name__} is not allowed in expressions")


def _eval(node: ast.AST, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(value, context) for value in node.values)
        return any(_eval(value, context) for value in node.values)
    if isinstance(node, ast.UnaryOp):
        return not _eval(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, context)
            if not _COMPARATORS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in _LITERAL_NAMES:
            return _LITERAL_NAMES[node.id]
        return context[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(element, context) for element in node.elts]
    message = f"{type(node).__name__} is not allowed in expressions"  # pragma: no cover
    raise ExpressionError(message)  # pragma: no cover
