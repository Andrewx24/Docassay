"""Reading-order score.

Example:
    from docassay.scoring.order import score_order

    score_order(document, [("Bill To", "Line Items")])
"""

from __future__ import annotations

from collections.abc import Sequence

from docassay.models.document import Document
from docassay.normalize import norm_text, round4
from docassay.scoring import Failure, MetricResult, ScoreOptions
from docassay.scoring.text import document_text


def score_order(
    doc: Document,
    expect: Sequence[tuple[str, str]],
    opts: ScoreOptions | None = None,
) -> MetricResult:
    """Score pairwise "A before B" checks on the normalized document text.

    For each pair, the first string must occur in ``norm_text(document_text(doc))``.
    The second string must then occur at or after the end of that first match.
    A repeated first string uses the earliest match. The score is the fraction
    of pairs that pass, rounded to 4 decimals. An empty list scores 1.

    Example:
        score_order(document, [("Bill To", "Line Items")])
    """
    del opts
    if not expect:
        return MetricResult(score=round4(1.0), failures=[])
    raw = document_text(doc)
    haystack = norm_text(raw)
    failures: list[Failure] = []
    passed = 0
    for index, (before, after) in enumerate(expect):
        first = norm_text(before)
        second = norm_text(after)
        start = haystack.find(first)
        check = f"reading_order[{index}]"
        if start < 0:
            failures.append(_failure(check, "first string not found", before, after, raw))
            continue
        if haystack.find(second, start + len(first)) >= 0:
            passed += 1
            continue
        reason = "second string not found" if haystack.find(second) < 0 else "out of order"
        failures.append(_failure(check, reason, before, after, raw))
    return MetricResult(score=round4(passed / len(expect)), failures=failures)


def _failure(check: str, reason: str, before: str, after: str, raw: str) -> Failure:
    return Failure(
        metric="order",
        check=check,
        reason=reason,
        expected=[before, after],
        actual=raw,
    )
