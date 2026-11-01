"""Shared scorer results.

Example:
    from docassay.scoring import Failure, MetricResult

    result = MetricResult(score=1.0, failures=[])
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MetricName = Literal["tables", "text", "order", "fields", "chunks"]
DecimalMark = Literal[".", ","]
DateOrder = Literal["mdy", "dmy"]


class _Unset:
    """Marks a Failure field the caller did not set."""


_UNSET = _Unset()


@dataclass(frozen=True)
class ScoreOptions:
    """Options shared by every scorer. Defaults match the config file.

    Example:
        options = ScoreOptions(decimal=",", date_order="dmy")
    """

    decimal: DecimalMark = "."
    date_order: DateOrder = "mdy"


@dataclass(frozen=True)
class Failure:
    """One reason a score is below 1.

    Example:
        Failure(metric="tables", check="tables[0]", reason="no table found on page 2")
    """

    metric: MetricName
    check: str
    reason: str
    expected: object = _UNSET
    actual: object = _UNSET
    page: int | None = None

    def to_json(self) -> dict[str, object]:
        """Return the failure as JSON-ready data, omitting unset fields.

        Example:
            Failure("tables", "tables[0].shape", "shape does not match").to_json()
        """
        payload: dict[str, object] = {
            "metric": self.metric,
            "check": self.check,
            "reason": self.reason,
        }
        if not isinstance(self.expected, _Unset):
            payload["expected"] = self.expected
        if not isinstance(self.actual, _Unset):
            payload["actual"] = self.actual
        if self.page is not None:
            payload["page"] = self.page
        return payload


@dataclass(frozen=True)
class MetricResult:
    """A score in ``[0, 1]`` and the failures that explain anything below 1.

    Example:
        MetricResult(score=0.5, failures=[])
    """

    score: float
    failures: list[Failure]


__all__ = [
    "DateOrder",
    "DecimalMark",
    "Failure",
    "MetricName",
    "MetricResult",
    "ScoreOptions",
]
