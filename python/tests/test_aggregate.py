"""Aggregation math shared by the scorecard."""

import pytest

from docassay.scoring.aggregate import aggregate_scores, document_scores, mean, percentile


def test_mean_and_percentile() -> None:
    assert mean([1.0, 0.0]) == 0.5
    assert percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([10.0, 20.0, 30.0], 0.95) == 29.0
    with pytest.raises(ValueError, match="at least one"):
        mean([])
    with pytest.raises(ValueError, match="at least one"):
        percentile([], 0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        percentile([1.0], 1.5)


def test_document_and_summary_scores() -> None:
    assert document_scores({}) == {}
    assert document_scores({"tables": 1.0, "text": 0.0})["overall"] == 0.5
    summary = aggregate_scores([{"tables": 1.0, "overall": 1.0}, {"text": 0.0, "overall": 0.0}])
    assert summary == {"tables": 1.0, "text": 0.0, "overall": 0.5}
    assert aggregate_scores([]) == {}
