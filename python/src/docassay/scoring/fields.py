"""Extracted-field score.

Example:
    from docassay.scoring.fields import score_fields

    score_fields(document, {"total": 4120, "vendor": "Acme Corp"})
"""

from __future__ import annotations

from collections.abc import Mapping

from docassay.models.document import Document
from docassay.normalize import CompareOpts, norm_text, parse_number, round4, values_equal
from docassay.scoring import Failure, MetricResult, ScoreOptions

FieldValue = str | int | float | bool | None
_TRUE_WORDS = ("true", "yes")
_FALSE_WORDS = ("false", "no")


def score_fields(
    doc: Document,
    expect: Mapping[str, FieldValue],
    opts: ScoreOptions | None = None,
) -> MetricResult:
    """Score extracted fields against the golden map, in golden key order.

    A missing ``doc.fields`` fails every key with ``"parser returned no fields"``.
    A missing key fails with ``"field missing"``, except a null expectation,
    which also passes when the value is null or blank after normalization.
    Booleans accept the same boolean or the strings true/false/yes/no.
    A golden number uses the numeric rule on that number and on the parsed
    actual text. Strings use ``values_equal``. A compared value that fails is
    ``"value does not match"``. The score is the fraction that pass, rounded
    to 4 decimals. An empty map scores 1.

    Example:
        score_fields(document, {"total": 4120}, ScoreOptions(decimal="."))
    """
    options = opts or ScoreOptions()
    if not expect:
        return MetricResult(score=round4(1.0), failures=[])
    failures: list[Failure] = []
    passed = 0
    fields = doc.fields
    for key, expected in expect.items():
        if fields is None:
            failures.append(_failure(key, "parser returned no fields", expected, None))
            continue
        if key not in fields:
            if expected is None:
                passed += 1
                continue
            failures.append(_failure(key, "field missing", expected, None))
            continue
        actual = fields[key].value
        if _matches(expected, actual, options):
            passed += 1
            continue
        failures.append(_failure(key, "value does not match", expected, actual))
    return MetricResult(score=round4(passed / len(expect)), failures=failures)


def _matches(expected: FieldValue, actual: object, options: ScoreOptions) -> bool:
    if expected is None:
        if actual is None:
            return True
        return norm_text(_stringify(actual)) == ""
    if isinstance(expected, bool):
        if isinstance(actual, bool):
            return actual == expected
        if isinstance(actual, str):
            token = norm_text(actual)
            return token in (_TRUE_WORDS if expected else _FALSE_WORDS)
        return False
    if isinstance(expected, int | float):
        return _numbers_equal(expected, actual, options)
    if actual is None:
        return False
    compare: CompareOpts = {"decimal": options.decimal, "date_order": options.date_order}
    return values_equal(_stringify(expected), _stringify(actual), compare)


def _numbers_equal(expected: int | float, actual: object, options: ScoreOptions) -> bool:
    """Compare a golden number with the values_equal numeric tolerance.

    The golden number is already numeric, so it is not parsed again. Actual
    text is parsed with the configured decimal mark.
    """
    if isinstance(actual, bool) or actual is None:
        return False
    if isinstance(actual, int | float):
        actual_number = float(actual)
    elif isinstance(actual, str):
        parsed = parse_number(actual, options.decimal)
        if parsed is None:
            return False
        actual_number = parsed
    else:
        return False
    return abs(float(expected) - actual_number) <= 1e-6 * max(1.0, abs(actual_number))


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _failure(key: str, reason: str, expected: object, actual: object) -> Failure:
    return Failure(
        metric="fields",
        check=f"fields[{key}]",
        reason=reason,
        expected=expected,
        actual=actual,
    )
