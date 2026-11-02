"""JSON scorecard.

The object validates against ``scorecard.schema.json``. ``runId`` and
``createdAt`` are the only fields that change between identical runs. Integral
numbers are written without a trailing ``.0`` so Python and TypeScript match.

Example:
    from docassay.report.json import render_json

    render_json(card)
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from docassay.models.scorecard import DocumentResult, Failure, MetricScores, ParserResult, Scorecard
from docassay.report.terminal import metric_value
from docassay.scoring.aggregate import METRICS

_SCORE_KEYS = (*METRICS, "overall")


def render_json(card: Scorecard) -> str:
    """Return ``card`` as indented JSON with a trailing newline.

    Example:
        text = render_json(card)
    """
    return json.dumps(scorecard_dict(card), indent=2, ensure_ascii=False) + "\n"


def scorecard_dict(card: Scorecard) -> dict[str, Any]:
    """Return ``card`` as JSON-ready data with stable key order.

    Example:
        scorecard_dict(card)["specVersion"] == "1.0.0"
    """
    return {
        "specVersion": card.spec_version,
        "docassayVersion": card.docassay_version,
        "runId": card.run_id,
        "createdAt": format_created(card.created_at),
        "parsers": [_parser_dict(parser) for parser in card.parsers],
    }


def format_created(moment: datetime) -> str:
    """Return ``moment`` as UTC ``YYYY-MM-DDTHH:MM:SSZ``.

    Example:
        format_created(datetime(2026, 10, 26, 15, tzinfo=timezone.utc))
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser_dict(parser: ParserResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parser": parser.parser,
        "adapter": parser.adapter,
        "parserVersion": parser.parser_version,
        "summary": _scores(parser.summary),
    }
    if "cost_usd" in parser.model_fields_set and parser.cost_usd is not None:
        payload["costUsd"] = _number(parser.cost_usd)
    if "latency_ms_p50" in parser.model_fields_set and parser.latency_ms_p50 is not None:
        payload["latencyMsP50"] = _number(parser.latency_ms_p50)
    if "latency_ms_p95" in parser.model_fields_set and parser.latency_ms_p95 is not None:
        payload["latencyMsP95"] = _number(parser.latency_ms_p95)
    payload["documents"] = [_document_dict(document) for document in parser.documents]
    return payload


def _document_dict(document: DocumentResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": document.id, "file": document.file}
    if document.error:
        payload["error"] = document.error
    payload["scores"] = _scores(document.scores)
    payload["failures"] = [_failure_dict(failure) for failure in document.failures]
    return payload


def _scores(scores: MetricScores) -> dict[str, int | float]:
    payload: dict[str, int | float] = {}
    for name in _SCORE_KEYS:
        value = metric_value(scores, name)
        if value is not None:
            payload[name] = _number(value)
    return payload


def _failure_dict(failure: Failure) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metric": failure.metric.value,
        "check": failure.check,
        "reason": failure.reason,
    }
    if "expected" in failure.model_fields_set:
        payload["expected"] = _json_ready(failure.expected)
    if "actual" in failure.model_fields_set:
        payload["actual"] = _json_ready(failure.actual)
    if "page" in failure.model_fields_set and failure.page is not None:
        payload["page"] = failure.page
    return payload


def _json_ready(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    return str(value)


def _number(value: float) -> int | float:
    if math.isfinite(value) and value == math.trunc(value) and abs(value) <= 2**53:
        return int(value)
    return value
