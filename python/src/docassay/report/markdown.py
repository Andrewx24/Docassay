"""Markdown scorecard for a pull request comment.

The first line is the hidden marker ``<!-- docassay-report -->``. The body is
a compact table plus a collapsible failure list.

Example:
    from docassay.report.markdown import render_markdown

    render_markdown(card, [3])
"""

from __future__ import annotations

from collections.abc import Sequence

from docassay.adapters.runner_support import package_version
from docassay.models.scorecard import Failure, Scorecard
from docassay.report.terminal import (
    cost_per_thousand,
    format_cost,
    format_latency,
    format_score4,
    metric_value,
    sorted_parsers,
)
from docassay.scoring.aggregate import METRICS

MARKER = "<!-- docassay-report -->"
_HEADERS = ["parser", *METRICS, "overall", "$/1k pages", "p95"]


def render_markdown(card: Scorecard, pages: Sequence[int]) -> str:
    """Return a GitHub-flavored Markdown report.

    Example:
        text = render_markdown(card, [1])
    """
    count = len(card.parsers[0].documents) if card.parsers else 0
    lines = [
        MARKER,
        "",
        f"# docassay {package_version()}",
        "",
        f"{_phrase(count, 'document')} · {_phrase(len(card.parsers), 'parser')}",
        "",
        "| " + " | ".join(_HEADERS) + " |",
        "| " + " | ".join("---" for _header in _HEADERS) + " |",
    ]
    failures: list[str] = []
    for parser, page_count in sorted_parsers(card, pages):
        cells = [parser.parser]
        for name in (*METRICS, "overall"):
            cells.append(format_score4(metric_value(parser.summary, name)))
        cells.append(format_cost(cost_per_thousand(parser.cost_usd, page_count)))
        cells.append(format_latency(parser.latency_ms_p95))
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
        for document in parser.documents:
            if document.error:
                failures.append(
                    f"- **{_cell(parser.parser)}** `{_cell(document.id)}` error: "
                    f"{_cell(document.error)}"
                )
            for failure in document.failures:
                failures.append(_failure(parser.parser, document.id, failure))
    lines.append("")
    lines.append("<details>")
    lines.append(f"<summary>Failures ({len(failures)})</summary>")
    lines.append("")
    if failures:
        lines.extend(failures)
    else:
        lines.append("None.")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def _failure(parser: str, doc_id: str, failure: Failure) -> str:
    return (
        f"- **{_cell(parser)}** `{_cell(doc_id)}` {failure.metric.value} "
        f"`{_cell(failure.check)}`: {_cell(failure.reason)}"
    )


def _phrase(count: int, word: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {word}{suffix}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
