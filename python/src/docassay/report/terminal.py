"""Terminal scorecard.

Columns follow the CLI page: parser, the five metrics, overall, dollars per
1,000 pages, and p95 latency. ``—`` is a metric no golden file labels for that
parser. Rows are sorted by overall descending, then parser name.

Example:
    from docassay.report.terminal import render_terminal

    print(render_terminal(card, [3]))
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from docassay.adapters.runner_support import package_version
from docassay.models.scorecard import Failure, MetricScores, ParserResult, Scorecard
from docassay.scoring.aggregate import METRICS

EM_DASH = "\u2014"
_HEADERS = ["parser", *METRICS, "overall", "$/1k pages", "p95"]
_RIGHT = {"$/1k pages", "p95"}


def render_terminal(card: Scorecard, pages: Sequence[int], *, verbose: bool = False) -> str:
    """Render ``card`` as the terminal table, worst-documents section, and failures.

    ``pages`` is one page total per parser, in the same order as ``card.parsers``.
    It is the denominator for ``$/1k pages`` and is not stored on the scorecard.
    ``verbose`` prints every failure. Otherwise the last line points at ``-v``
    when anything scored below 1.

    Example:
        render_terminal(card, [1, 2], verbose=True)
    """
    ordered = sorted_parsers(card, pages)
    count = len(card.parsers[0].documents) if card.parsers else 0
    lines = [
        (
            f"docassay {package_version()} · {_plural(count, 'document')}"
            f" · {_plural(len(card.parsers), 'parser')}"
        ),
        "",
    ]
    rows = [_row(parser, page_count) for parser, page_count in ordered]
    lines.extend(_render_columns(_HEADERS, rows))
    worst = worst_lines(card)
    if worst:
        lines.extend(["", "Worst documents", *worst])
    failures = failure_lines(ordered)
    if verbose and failures:
        lines.extend(["", "Failures", *failures])
    elif not verbose and (worst or failures):
        lines.extend(["", "Run with -v for every failure."])
    return "\n".join(lines) + "\n"


def sorted_parsers(card: Scorecard, pages: Sequence[int]) -> list[tuple[ParserResult, int]]:
    """Return parsers and page totals sorted by overall descending, then name.

    Example:
        sorted_parsers(card, [3])
    """
    if len(pages) != len(card.parsers):
        raise ValueError(
            f"page counts ({len(pages)}) must match parsers ({len(card.parsers)}). "
            "Pass one total per parser."
        )
    pairs = list(zip(card.parsers, pages, strict=True))

    def sort_key(item: tuple[ParserResult, int]) -> tuple[float, str]:
        overall = metric_value(item[0].summary, "overall")
        rank = -1.0 if overall is None else overall
        return (-rank, item[0].parser)

    return sorted(pairs, key=sort_key)


def metric_value(scores: MetricScores, name: str) -> float | None:
    """Return one score as a float, or ``None`` when the metric is absent.

    Example:
        metric_value(card.parsers[0].summary, "tables")
    """
    value = getattr(scores, name)
    if value is None:
        return None
    root = getattr(value, "root", value)
    if isinstance(root, bool) or not isinstance(root, int | float):
        return None
    return float(root)


def format_score(value: float | None) -> str:
    """Format a score with two decimals, or an em dash when it is absent.

    Example:
        format_score(0.9) == "0.90"
    """
    if value is None:
        return EM_DASH
    return f"{_round_digits(value, 2):.2f}"


def format_score4(value: float | None) -> str:
    """Format a score with up to four decimals, or an em dash when it is absent.

    Example:
        format_score4(0.5) == "0.5"
    """
    if value is None:
        return EM_DASH
    text = f"{_round_digits(value, 4):.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_cost(value: float | None) -> str:
    """Format dollars with two decimals, or an em dash when cost is unknown.

    Example:
        format_cost(0) == "0.00"
    """
    if value is None:
        return EM_DASH
    return f"{_round_digits(value, 2):.2f}"


def format_latency(milliseconds: float | None) -> str:
    """Format latency in seconds with one decimal, or an em dash when unknown.

    Example:
        format_latency(6100) == "6.1s"
    """
    if milliseconds is None:
        return EM_DASH
    seconds = _round_digits(milliseconds / 1000, 1)
    return f"{seconds:.1f}s"


def cost_per_thousand(cost: float | None, pages: int) -> float | None:
    """Return ``cost * 1000 / pages``, or ``None`` when either side is missing.

    Example:
        cost_per_thousand(0.02, 2) == 10.0
    """
    if cost is None or pages <= 0:
        return None
    return cost * 1000 / pages


def worst_lines(card: Scorecard) -> list[str]:
    """Return one line per document, using the metric with the lowest score.

    A document is omitted when every present score is 1. The line lists the
    parsers that scored below 1, lowest score first.

    Example:
        worst_lines(card)
    """
    grouped: dict[str, list[tuple[str, MetricScores]]] = {}
    for parser in card.parsers:
        for document in parser.documents:
            grouped.setdefault(document.id, []).append((parser.parser, document.scores))
    entries: list[tuple[float, str, str, list[tuple[str, float]]]] = []
    for doc_id, rows in grouped.items():
        chosen: tuple[float, str, list[tuple[str, float]]] | None = None
        for metric in METRICS:
            pairs: list[tuple[str, float]] = []
            for name, scores in rows:
                value = metric_value(scores, metric)
                if value is not None:
                    pairs.append((name, value))
            if not pairs:
                continue
            lowest = min(score for _name, score in pairs)
            if chosen is None or lowest < chosen[0]:
                chosen = (lowest, metric, pairs)
        if chosen is None or chosen[0] >= 1:
            continue
        shown = [(name, score) for name, score in chosen[2] if score < 1]
        shown.sort(key=lambda item: (item[1], item[0]))
        if shown:
            entries.append((chosen[0], doc_id, chosen[1], shown))
    entries.sort(key=lambda item: (item[0], item[1]))
    if not entries:
        return []
    width = max(len(item[1]) for item in entries)
    lines: list[str] = []
    for _lowest, doc_id, metric, shown in entries:
        body = ", ".join(f"{format_score(score)} ({name})" for name, score in shown)
        lines.append(f"  {doc_id.ljust(width)}  {metric} {body}")
    return lines


def failure_lines(ordered: Sequence[tuple[ParserResult, int]]) -> list[str]:
    """Return one line per adapter error and per scoring failure.

    Example:
        failure_lines([(parser, 1)])
    """
    lines: list[str] = []
    for parser, _pages in ordered:
        for document in parser.documents:
            if document.error:
                lines.append(f"  {parser.parser}  {document.id}  error  {document.error}")
            for failure in document.failures:
                lines.append(_failure_line(parser.parser, document.id, failure))
    return lines


def _failure_line(parser: str, doc_id: str, failure: Failure) -> str:
    metric = failure.metric.value
    return f"  {parser}  {doc_id}  {metric}  {failure.check}  {failure.reason}"


def _row(parser: ParserResult, pages: int) -> list[str]:
    cells = [parser.parser]
    for name in (*METRICS, "overall"):
        cells.append(format_score(metric_value(parser.summary, name)))
    cells.append(format_cost(cost_per_thousand(parser.cost_usd, pages)))
    cells.append(format_latency(parser.latency_ms_p95))
    return cells


def _render_columns(headers: list[str], rows: list[list[str]]) -> list[str]:
    value_widths = [
        max((len(row[index]) for row in rows), default=0) for index in range(len(headers))
    ]
    widths = [max(len(header), value_widths[index]) for index, header in enumerate(headers)]
    lines = [_join([header.ljust(widths[index]) for index, header in enumerate(headers)])]
    for row in rows:
        cells: list[str] = []
        for index, value in enumerate(row):
            width = widths[index]
            if headers[index] in _RIGHT:
                cells.append(value.rjust(value_widths[index]).ljust(width))
            else:
                cells.append(value.ljust(width))
        lines.append(_join(cells))
    return lines


def _join(cells: list[str]) -> str:
    return "  ".join(cells).rstrip()


def _plural(count: int, word: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {word}{suffix}"


def _round_digits(value: float, digits: int) -> float:
    scale = float(10**digits)
    return float(math.floor(value * scale + 0.5)) / scale
