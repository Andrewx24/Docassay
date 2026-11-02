"""Combine metric scores into an overall score.

``doc.overall`` is the mean of the metrics present on that document.
``summary[metric]`` is the mean of that metric over documents that have it.
``summary.overall`` is the mean of the document overalls. Cost and latency
stay out of every score.

Example:
    from docassay.scoring.aggregate import aggregate_scores, document_scores

    scores = document_scores({"tables": 1.0, "text": 0.0})
    aggregate_scores([scores])
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from docassay.normalize import round4

METRICS: tuple[str, ...] = ("tables", "text", "order", "fields", "chunks")
_SUMMARY: tuple[str, ...] = (*METRICS, "overall")


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean of ``values``.

    Example:
        mean([1.0, 0.0]) == 0.5
    """
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(values) / len(values)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the linear percentile of ``values``.

    ``fraction`` is between 0 and 1. One sample returns that sample. With more
    samples the rank is ``fraction * (count - 1)`` and the result interpolates
    the two surrounding values. Latency percentiles use this so Python and
    TypeScript agree.

    Example:
        percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    """
    if not values:
        raise ValueError("percentile requires at least one value")
    if fraction < 0 or fraction > 1:
        raise ValueError("percentile fraction must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = fraction * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def document_scores(metric_scores: Mapping[str, float]) -> dict[str, float]:
    """Copy present metrics and set ``overall`` to their mean.

    The overall is rounded to 4 decimals. A document with no metrics has no
    overall. An adapter failure passes 0 for every metric the golden file
    defines, and the overall is then 0.

    Example:
        document_scores({"tables": 1.0, "text": 0.0})["overall"] == 0.5
    """
    scores = {name: metric_scores[name] for name in METRICS if name in metric_scores}
    present = [scores[name] for name in METRICS if name in scores]
    if present:
        scores["overall"] = round4(mean(present))
    return scores


def aggregate_scores(per_document: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """Mean each metric over the documents that have it.

    ``overall`` is the mean of document overalls, not the mean of the summary
    metrics. Each mean is rounded to 4 decimals. An empty list returns ``{}``.

    Example:
        aggregate_scores([{"tables": 1.0, "overall": 1.0}, {"text": 0.0, "overall": 0.0}])
    """
    summary: dict[str, float] = {}
    for name in _SUMMARY:
        found = [item[name] for item in per_document if name in item]
        if found:
            summary[name] = round4(mean(found))
    return summary
