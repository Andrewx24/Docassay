"""Normalization used by every comparison.

Example:
    from docassay.normalize import values_equal

    values_equal("Nov. 1 2026", "2026-11-01")
"""

from __future__ import annotations

import math
from typing import Literal, TypedDict

from docassay.normalize.dates import parse_date
from docassay.normalize.numbers import parse_number
from docassay.normalize.text import norm_text

__all__ = [
    "CompareOpts",
    "norm_text",
    "parse_date",
    "parse_number",
    "round4",
    "values_equal",
]


class CompareOpts(TypedDict, total=False):
    """Options for `values_equal`.

    `decimal` is `"."` or `","`. `date_order` is `"mdy"` or `"dmy"`.
    """

    decimal: Literal[".", ","]
    date_order: Literal["mdy", "dmy"]


def round4(value: float) -> float:
    """Round to 4 decimal places with floor(x * 10000 + 0.5) / 10000.

    Example:
        round4(1.23456) == 1.2346
    """
    return math.floor(value * 10000 + 0.5) / 10000


def values_equal(expected: str, actual: str, opts: CompareOpts | None = None) -> bool:
    """Compare two values as numbers, then dates, then normalized text.

    A number is never equal to a date. Numeric equality is
    abs(a - b) <= 1e-6 * max(1, abs(b)), with b the actual value.

    Example:
        values_equal("(350)", "-350") is True
    """
    options = opts or {}
    decimal = options.get("decimal", ".")
    order = options.get("date_order", "mdy")
    expected_number = parse_number(expected, decimal)
    actual_number = parse_number(actual, decimal)
    if expected_number is not None and actual_number is not None:
        return abs(expected_number - actual_number) <= 1e-6 * max(1.0, abs(actual_number))
    expected_date = parse_date(expected, order)
    actual_date = parse_date(actual, order)
    if expected_date is not None and actual_date is not None:
        return expected_date == actual_date
    number_vs_date = (expected_number is not None and actual_date is not None) or (
        expected_date is not None and actual_number is not None
    )
    if number_vs_date:
        return False
    return norm_text(expected) == norm_text(actual)
