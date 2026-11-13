"""Text containment score.

Example:
    from docassay.scoring.text import document_text, score_text

    score_text(document, ["Payment due"], ScoreOptions())
"""

from __future__ import annotations

from collections.abc import Sequence

from docassay.models.document import Document, Table
from docassay.normalize import norm_text, round4
from docassay.scoring import Failure, MetricResult, ScoreOptions


def document_text(doc: Document) -> str:
    """Build the full document text used by text and reading-order scores.

    Block texts stay in array order and are joined with ``\\n\\n``, including
    blocks whose text is empty. Each table contributes its header row when
    ``header`` is present, then its body rows. Cells in a row are joined with
    ``" | "`` and rows with ``\\n``. Tables are joined with ``\\n\\n``. The
    block text and the table text are joined with ``\\n\\n`` when each side is
    a non-empty string. A table's ``markdown`` is left out.

    Example:
        document_text(document)
    """
    parts: list[str] = []
    if doc.blocks:
        parts.append("\n\n".join(block.text for block in doc.blocks))
    if doc.tables:
        parts.append("\n\n".join(_render_table(table) for table in doc.tables))
    return "\n\n".join(part for part in parts if part != "")


def score_text(
    doc: Document,
    expect: Sequence[str],
    opts: ScoreOptions | None = None,
) -> MetricResult:
    """Score the fraction of strings found in the normalized document text.

    ``norm_text(s)`` must be a substring of ``norm_text(document_text(doc))``.
    Matching is exact after normalization. The score is that fraction, rounded
    to 4 decimals. An empty list scores 1.

    Example:
        score_text(document, ["Payment due within 30 days"])
    """
    del opts
    if not expect:
        return MetricResult(score=round4(1.0), failures=[])
    raw = document_text(doc)
    haystack = norm_text(raw)
    failures: list[Failure] = []
    passed = 0
    for index, item in enumerate(expect):
        if norm_text(item) in haystack:
            passed += 1
            continue
        failures.append(
            Failure(
                metric="text",
                check=f"text_contains[{index}]",
                reason="text not found",
                expected=item,
                actual=raw,
            )
        )
    return MetricResult(score=round4(passed / len(expect)), failures=failures)


def _render_table(table: Table) -> str:
    lines: list[str] = []
    if table.header is not None:
        lines.append(" | ".join(table.header))
    for row in table.rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)
