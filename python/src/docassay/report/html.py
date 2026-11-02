"""Self-contained HTML scorecard.

The file embeds its CSS and script. Filters hide rows by parser, tag, and
metric. Nothing is loaded from the network.

Example:
    from docassay.report.html import render_html

    render_html(card, [3], {"invoice-acme": ("invoice",)})
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from docassay.adapters.runner_support import package_version
from docassay.models.scorecard import DocumentResult, ParserResult, Scorecard
from docassay.report.terminal import (
    cost_per_thousand,
    format_cost,
    format_latency,
    format_score4,
    metric_value,
    sorted_parsers,
)
from docassay.scoring.aggregate import METRICS

_HEADERS = ["parser", "document", *METRICS, "overall", "error"]
_LEFT = {0, 1, len(_HEADERS) - 1}


def render_html(
    card: Scorecard,
    pages: Sequence[int],
    tags: Mapping[str, Sequence[str]],
) -> str:
    """Return one HTML document with parser, tag, and metric filters.

    Example:
        render_html(card, [1], {})
    """
    version = package_version()
    count = len(card.parsers[0].documents) if card.parsers else 0
    ordered = sorted_parsers(card, pages)
    parser_names = [parser.parser for parser, _page_count in ordered]
    tag_names = sorted({tag for values in tags.values() for tag in values})
    rows = [_summary_row(parser, page_count) for parser, page_count in ordered]
    for parser, _page_count in ordered:
        for document in parser.documents:
            rows.append(
                _document_row(parser.parser, document.id, document, tags.get(document.id, ()))
            )
    title = f"docassay {version}"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_text(title)}</title>\n<style>\n{_STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{_text(title)}</h1>\n"
        f"<p>{_phrase(count, 'document')} · {_phrase(len(card.parsers), 'parser')}</p>\n"
        '<div class="filters">\n'
        f"<label>Parser {_select('parser', parser_names)}</label>\n"
        f"<label>Tag {_select('tag', tag_names)}</label>\n"
        f"<label>Metric {_select('metric', list(METRICS))}</label>\n"
        "</div>\n<table>\n<thead><tr>"
        + "".join(_header(index, header) for index, header in enumerate(_HEADERS))
        + "</tr></thead>\n<tbody>\n"
        + "\n".join(rows)
        + "\n</tbody>\n</table>\n<script>\n"
        + _SCRIPT
        + "</script>\n</body>\n</html>\n"
    )


def _summary_row(parser: ParserResult, pages: int) -> str:
    cost = format_cost(cost_per_thousand(parser.cost_usd, pages))
    latency = format_latency(parser.latency_ms_p95)
    label = f"summary · {cost} /1k pages · p95 {latency}"
    cells = "".join(
        f"<td>{_text(format_score4(metric_value(parser.summary, name)))}</td>"
        for name in (*METRICS, "overall")
    )
    return (
        f'<tr data-parser="{_text(parser.parser)}" data-tags="" data-metrics="" data-kind="summary">'
        f'<td class="left">{_text(parser.parser)}</td>'
        f'<td class="left">{_text(label)}</td>{cells}<td></td></tr>'
    )


def _document_row(parser: str, doc_id: str, document: DocumentResult, tags: Sequence[str]) -> str:
    metrics = [name for name in METRICS if metric_value(document.scores, name) is not None]
    cells = "".join(
        f"<td>{_text(format_score4(metric_value(document.scores, name)))}</td>"
        for name in (*METRICS, "overall")
    )
    return (
        f'<tr data-parser="{_text(parser)}" data-tags="{_text(_join_tags(tags))}"'
        f' data-metrics="{_text(" ".join(metrics))}" data-kind="document">'
        f'<td class="left">{_text(parser)}</td>'
        f'<td class="left">{_text(doc_id)}</td>{cells}'
        f'<td class="left">{_text(document.error or "")}</td></tr>'
    )


def _header(index: int, header: str) -> str:
    kind = ' class="left"' if index in _LEFT else ""
    return f"<th{kind}>{_text(header)}</th>"


def _select(element_id: str, options: Sequence[str]) -> str:
    parts = ['<option value="all">all</option>']
    for option in options:
        parts.append(f'<option value="{_text(option)}">{_text(option)}</option>')
    return f'<select id="{element_id}">' + "".join(parts) + "</select>"


def _join_tags(tags: Sequence[str]) -> str:
    return "\u001f".join(tags)


def _phrase(count: int, word: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {word}{suffix}"


def _text(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


_STYLE = (
    "body{font:14px/1.4 sans-serif;margin:24px;color:#1b1b1b}\n"
    "table{border-collapse:collapse;width:100%}\n"
    "th,td{border:1px solid #ccc;padding:4px 8px;text-align:right}\n"
    "th.left,td.left{text-align:left}\n"
    ".filters{display:flex;gap:16px;margin:12px 0}\n"
    "tr[hidden]{display:none}\n"
)

_SCRIPT = """\
const parser = document.getElementById("parser");
const tag = document.getElementById("tag");
const metric = document.getElementById("metric");
function apply() {
  const parserValue = parser.value;
  const tagValue = tag.value;
  const metricValue = metric.value;
  document.querySelectorAll("tbody tr").forEach(function (row) {
    const parserOk = parserValue === "all" || row.dataset.parser === parserValue;
    const tags = (row.dataset.tags || "").split("\\u001f").filter(Boolean);
    const tagOk = tagValue === "all" || tags.indexOf(tagValue) !== -1;
    const metrics = (row.dataset.metrics || "").split(" ").filter(Boolean);
    const metricOk = metricValue === "all" || metrics.indexOf(metricValue) !== -1;
    const summary = row.dataset.kind === "summary";
    row.hidden = summary ? !parserOk : !(parserOk && tagOk && metricOk);
  });
}
parser.addEventListener("change", apply);
tag.addEventListener("change", apply);
metric.addEventListener("change", apply);
"""
