"""Parser adapters.

Example:
    from docassay.adapters import resolve

    adapter = resolve("file")
"""

from docassay.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterUnavailable,
    RawResult,
)
from docassay.adapters.registry import resolve, valid_ids
from docassay.adapters.runner_support import ensure_available, run_parser

__all__ = [
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterUnavailable",
    "RawResult",
    "ensure_available",
    "resolve",
    "run_parser",
    "valid_ids",
]
