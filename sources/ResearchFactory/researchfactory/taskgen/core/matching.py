"""Recursive matching for planner context and resource constraints."""
from __future__ import annotations

import operator
from collections.abc import Mapping, Sequence
from typing import Any, Callable

MISSING = object()
_OPERATORS = {"eq", "one_of", "contains", "min", "max", "gte", "lte"}
_BOUNDS: tuple[tuple[str, Callable[[Any, Any], bool]], ...] = (
    ("min", operator.ge),
    ("gte", operator.ge),
    ("max", operator.le),
    ("lte", operator.le),
)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _choices(value: Any) -> Sequence[Any]:
    return value if _is_sequence(value) else [value]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _operators_match(actual: Any, expected: Mapping[str, Any]) -> bool:
    if "eq" in expected and not value_matches(actual, expected["eq"]):
        return False
    if "one_of" in expected and not any(
        value_matches(actual, choice) for choice in _choices(expected["one_of"])
    ):
        return False
    if "contains" in expected and not all(
        value_matches(actual, item) for item in _choices(expected["contains"])
    ):
        return False
    return all(
        key not in expected
        or (_number(actual) and _number(expected[key]) and compare(actual, expected[key]))
        for key, compare in _BOUNDS
    )


def value_matches(actual: Any, expected: Any) -> bool:
    """Match scalar, nested, sequence, and operator constraint forms."""
    if actual is MISSING:
        return False
    if isinstance(expected, Mapping):
        if expected and set(expected).issubset(_OPERATORS):
            return _operators_match(actual, expected)
        return isinstance(actual, Mapping) and all(
            value_matches(actual.get(key, MISSING), value)
            for key, value in expected.items()
        )
    if _is_sequence(expected):
        return (
            all(any(value_matches(item, wanted) for item in actual) for wanted in expected)
            if _is_sequence(actual)
            else any(value_matches(actual, choice) for choice in expected)
        )
    return (
        any(value_matches(item, expected) for item in actual)
        if _is_sequence(actual)
        else actual == expected
    )


def constraints_match(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(value_matches(actual.get(key, MISSING), value) for key, value in expected.items())
