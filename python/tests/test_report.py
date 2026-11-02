"""Terminal, JSON, Markdown, and HTML scorecards."""

import copy
import json
from datetime import datetime, timezone

import pytest

from docassay.models.scorecard import Scorecard
from docassay.report.html import render_html
from docassay.report.json import format_created, render_json
from docassay.report.markdown import MARKER, render_markdown
from docassay.report.terminal import (
    EM_DASH,
    cost_per_thousand,
    format_cost,
    format_latency,
    render_terminal,
    sorted_parsers,
)


class _Mark:
    def __str__(self) -> str:
        return "mark"


CARD = {
    "specVersion": "1.0.0",
    "docassayVersion": "0.0.0",
    "runId": "run_test",
    "createdAt": "2026-11-01T18:00:00Z",
    "parsers": [
        {
            "parser": "docling",
            "adapter": "docling",
            "parserVersion": "1",
            "summary": {"tables": 0.81, "text": 0.97, "order": 0.92, "overall": 0.9},
            "costUsd": 0,
            "latencyMsP50": 1000,
            "latencyMsP95": 6100,
            "documents": [
                {
                    "id": "scan-lease-2019",
                    "file": "files/scan.pdf",
                    "scores": {"tables": 0.42, "overall": 0.42},
                    "failures": [
                        {"metric": "tables", "check": "tables[0]", "reason": "no table found"}
                    ],
                }
            ],
        },
        {
            "parser": "llamaparse:agentic",
            "adapter": "llamaparse:agentic",
            "parserVersion": "2026-09-24",
            "summary": {
                "tables": 0.94,
                "text": 0.99,
                "order": 0.95,
                "fields": 0.96,
                "overall": 0.96,
            },
            "costUsd": 0.025,
            "latencyMsP50": 800,
            "latencyMsP95": 48000,
            "documents": [
                {
                    "id": "scan-lease-2019",
                    "file": "files/scan.pdf",
                    "scores": {"tables": 0.58, "overall": 0.58},
                    "failures": [
                        {
                            "metric": "tables",
                            "check": "tables[0]",
                            "reason": "header does not match",
                        }
                    ],
                }
            ],
        },
    ],
}


def _card() -> Scorecard:
    return Scorecard.model_validate(CARD)


def test_terminal_matches_the_cli_page() -> None:
    page = copy.deepcopy(CARD)
    page["parsers"][1]["costUsd"] = 0.25
    page["parsers"].append(
        {
            "parser": "reducto",
            "adapter": "reducto",
            "parserVersion": "1",
            "summary": {
                "tables": 0.95,
                "text": 0.99,
                "order": 0.96,
                "fields": 0.98,
                "chunks": 0.91,
                "overall": 0.96,
            },
            "costUsd": 0.2,
            "latencyMsP95": 9100,
            "documents": [
                {
                    "id": "scan-lease-2019",
                    "file": "files/scan.pdf",
                    "scores": {"tables": 1, "overall": 1},
                    "failures": [],
                },
                {
                    "id": "10k-segment-table",
                    "file": "files/table.pdf",
                    "scores": {"order": 1, "overall": 1},
                    "failures": [],
                },
            ],
        }
    )
    page["parsers"][0]["documents"].append(
        {
            "id": "10k-segment-table",
            "file": "files/table.pdf",
            "scores": {"order": 0.5, "overall": 0.5},
            "failures": [{"metric": "order", "check": "order[0]", "reason": "out of order"}],
        }
    )
    page["parsers"][1]["documents"].append(
        {
            "id": "10k-segment-table",
            "file": "files/table.pdf",
            "scores": {"order": 1, "overall": 1},
            "failures": [],
        }
    )
    text = render_terminal(Scorecard.model_validate(page), [20, 20, 20])
    assert "\n".join(text.splitlines()[2:10]) == (
        "parser              tables  text  order  fields  chunks  overall  $/1k pages  p95\n"
        "llamaparse:agentic  0.94    0.99  0.95   0.96    —       0.96     12.50       48.0s\n"
        "reducto             0.95    0.99  0.96   0.98    0.91    0.96     10.00        9.1s\n"
        "docling             0.81    0.97  0.92   —       —       0.90      0.00        6.1s\n"
        "\n"
        "Worst documents\n"
        "  scan-lease-2019    tables 0.42 (docling), 0.58 (llamaparse:agentic)\n"
        "  10k-segment-table  order 0.50 (docling)"
    )


def test_terminal_layout_sorts_and_marks_missing_metrics() -> None:
    text = render_terminal(_card(), [2, 2])
    assert text.startswith("docassay 0.0.0 · 1 document · 2 parsers\n")
    assert text.index("llamaparse:agentic") < text.index("docling")
    assert EM_DASH in text
    assert "12.50" in text
    assert "48.0s" in text
    assert "6.1s" in text
    assert "tables 0.42 (docling), 0.58 (llamaparse:agentic)" in text
    assert "Run with -v for every failure." in text
    assert "Failures" not in text


def test_verbose_lists_failures_and_a_perfect_run_stays_quiet() -> None:
    verbose = render_terminal(_card(), [2, 2], verbose=True)
    assert "no table found" in verbose
    assert "header does not match" in verbose
    assert "Run with -v" not in verbose
    perfect = {
        **CARD,
        "parsers": [
            {
                "parser": "file",
                "adapter": "file",
                "parserVersion": "file",
                "summary": {"text": 1, "overall": 1},
                "documents": [
                    {
                        "id": "note",
                        "file": "note.pdf",
                        "scores": {"text": 1, "overall": 1},
                        "failures": [],
                    }
                ],
            }
        ],
    }
    quiet = render_terminal(Scorecard.model_validate(perfect), [1])
    assert "Worst documents" not in quiet
    assert "Run with -v" not in quiet


def test_markdown_and_html_are_shareable() -> None:
    card = _card()
    markdown = render_markdown(card, [2, 2])
    assert markdown.startswith(MARKER + "\n")
    assert "<details>" in markdown
    assert "<summary>Failures (2)</summary>" in markdown
    assert "1 document · 2 parsers" in markdown
    named = Scorecard.model_validate(
        {
            **CARD,
            "parsers": [{**CARD["parsers"][0], "parser": "<b>"}],
        }
    )
    html = render_html(named, [2], {"scan-lease-2019": ("lease",)})
    assert "http" not in html
    assert "<link" not in html
    assert 'id="parser"' in html and 'id="tag"' in html and 'id="metric"' in html
    assert "&lt;b&gt;" in html
    assert "lease" in html


def test_json_uses_stable_numbers_and_utc_timestamps() -> None:
    payload = json.loads(render_json(_card()))
    assert payload["createdAt"] == "2026-11-01T18:00:00Z"
    assert payload["parsers"][0]["costUsd"] == 0
    assert payload["parsers"][1]["latencyMsP95"] == 48000
    assert "fields" not in payload["parsers"][0]["summary"]
    moment = datetime(2026, 11, 1, 18, 0, 0, 900000, tzinfo=timezone.utc)
    assert format_created(moment) == "2026-11-01T18:00:00Z"
    assert format_created(datetime(2026, 11, 1, 18, 0, 0)) == "2026-11-01T18:00:00Z"
    nested = Scorecard.model_validate(CARD)
    nested.parsers[0].documents[0].failures[0].expected = {
        "rows": [1.0, {"n": 2.5}],
        "flag": True,
        "mark": _Mark(),
    }
    encoded = json.loads(render_json(nested))
    assert encoded["parsers"][0]["documents"][0]["failures"][0]["expected"] == {
        "rows": [1, {"n": 2.5}],
        "flag": True,
        "mark": "mark",
    }
    assert format_cost(None) == EM_DASH
    assert format_latency(None) == EM_DASH
    assert cost_per_thousand(1.0, 0) is None
    with pytest.raises(ValueError, match="page counts"):
        sorted_parsers(_card(), [1])
