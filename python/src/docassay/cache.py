"""Parser output cache.

The cache key is the sha256 of the file bytes, the adapter id (with an
optional ``:variant``), and the first 16 hex characters of the sha256 of the
canonical JSON of ``cache_identity()``. Keys in that JSON are sorted at every
object and there is no whitespace.

Example:
    from docassay.cache import cache_path, file_sha256

    cache_path(Path(".docassay/cache"), file_sha256(b"hello"), "file", {"dir": "out"})
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from docassay.models.document import Document

CachePolicy = Literal["use", "no-cache", "refresh"]
_HEX = "0123456789abcdef"


class CacheError(ValueError):
    """A cache key or directory that cannot be used.

    Example:
        raise CacheError("file sha256 must be 64 lowercase hex characters")
    """


class CacheLookup:
    """Result of one cache read.

    ``message`` is the line ``-v`` prints on a hit. ``warning`` is set when a
    corrupt or schema-invalid entry was ignored. ``--no-cache`` and
    ``--refresh`` skip the read, so both fields stay ``None``.

    Example:
        lookup = lookup_cache(root, sha, "file", {"dir": "out"}, verbose=True)
    """

    def __init__(
        self,
        document: Document | None,
        message: str | None,
        warning: str | None,
    ) -> None:
        self.document = document
        self.message = message
        self.warning = warning


def file_sha256(data: bytes) -> str:
    """Return the lowercase hex sha256 of file bytes.

    Example:
        file_sha256(b"hello")
    """
    return hashlib.sha256(data).hexdigest()


def cache_path(
    root: Path,
    digest: str,
    adapter_id: str,
    identity: Mapping[str, Any],
) -> Path:
    """Return the JSON path for one file, adapter, and cache identity.

    Example:
        cache_path(Path(".docassay/cache"), file_sha256(b"hello"), "file", {})
    """
    _require_sha(digest)
    name = f"{_adapter_stem(adapter_id)}__{options_hash(identity)}.json"
    return root / digest[:2] / digest / name


def options_hash(identity: Mapping[str, Any]) -> str:
    """Return the first 16 hex characters of the identity's canonical JSON.

    Example:
        options_hash({"dir": "out"})
    """
    text = canonical_json(dict(identity))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def canonical_json(value: object) -> str:
    """Encode ``value`` as compact JSON with sorted object keys.

    Example:
        canonical_json({"b": 1, "a": None}) == '{"a":null,"b":1}'
    """
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def lookup_cache(
    root: Path,
    digest: str,
    adapter_id: str,
    identity: Mapping[str, Any],
    *,
    policy: CachePolicy = "use",
    verbose: bool = False,
) -> CacheLookup:
    """Read one cache entry.

    ``policy="no-cache"`` skips the read and still allows a later write.
    ``policy="refresh"`` skips the read so the next write overwrites the
    entry. A hit with ``verbose=True`` writes ``cache hit <id> <sha>`` to
    stderr and returns that line. Corrupt JSON and documents that fail the
    Document schema are ignored: a warning is written to stderr and the read
    is a miss so the caller can fetch again.

    Example:
        lookup_cache(root, sha, "file", {"dir": "out"}, policy="no-cache")
    """
    if policy not in ("use", "no-cache", "refresh"):
        raise CacheError(f'unknown cache policy {policy!r}. Use "use", "no-cache", or "refresh".')
    if policy != "use":
        return CacheLookup(None, None, None)
    path = cache_path(root, digest, adapter_id, identity)
    if not path.is_file():
        return CacheLookup(None, None, None)
    document, warning = _read_entry(path)
    if warning is not None:
        print(warning, file=sys.stderr)
        return CacheLookup(None, None, warning)
    message = None
    if verbose and document is not None:
        message = hit_message(adapter_id, digest)
        print(message, file=sys.stderr)
    return CacheLookup(document, message, None)


def write_cache(
    root: Path,
    digest: str,
    adapter_id: str,
    identity: Mapping[str, Any],
    document: Document,
) -> Path:
    """Store ``document`` atomically: write a temp file in the same directory, then rename.

    Example:
        write_cache(root, sha, "file", {"dir": "out"}, document)
    """
    path = cache_path(root, digest, adapter_id, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document.model_dump(mode="json", by_alias=True, exclude_unset=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def clear_cache(root: Path, parser_id: str | None = None) -> int:
    """Delete cached documents. ``parser_id`` limits the delete to that id and its variants.

    Example:
        clear_cache(Path(".docassay/cache"), "llamaparse")
    """
    if not root.exists():
        return 0
    if not root.is_dir():
        raise CacheError(
            f"cache directory {root} is not a directory. " "Pass a folder such as .docassay/cache."
        )
    prefix = None if parser_id is None else _adapter_stem(parser_id) + "__"
    removed = 0
    for path in sorted(root.rglob("*.json")):
        if not path.is_file() or not _is_entry_name(path.name):
            continue
        if prefix is not None and not path.name.startswith(prefix):
            continue
        path.unlink()
        removed += 1
    _prune_empty(root)
    return removed


def hit_message(adapter_id: str, digest: str) -> str:
    """Return the ``-v`` line for a cache hit.

    Example:
        hit_message("file", "ab" * 32)
    """
    return f"cache hit {adapter_id} {digest}"


def _read_entry(path: Path) -> tuple[Document | None, str | None]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, f"cache ignore {path.as_posix()}: invalid JSON"
    try:
        return Document.model_validate(loaded), None
    except ValidationError:
        return None, f"cache ignore {path.as_posix()}: does not match document.schema.json"


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CacheError("cache identity numbers must be finite.")
        if value.is_integer() and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CacheError(
                    "cache identity object keys must be strings. " f"Got {type(key).__name__}."
                )
            result[key] = _canonicalize(item)
        return result
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    raise CacheError(
        "cache identity must be JSON objects, arrays, strings, numbers, booleans, or null. "
        f"Got {type(value).__name__}."
    )


def _require_sha(digest: str) -> None:
    if len(digest) == 64 and all(char in _HEX for char in digest):
        return
    raise CacheError(
        f"file sha256 must be 64 lowercase hex characters, got {digest!r}. "
        "Hash the file bytes with sha256."
    )


def _adapter_stem(adapter_id: str) -> str:
    if adapter_id == "" or "/" in adapter_id or "\\" in adapter_id:
        raise CacheError(
            f"adapter id {adapter_id!r} is not a cache key. "
            "Use an id such as file or llamaparse:agentic."
        )
    return adapter_id.replace(":", "__")


def _is_entry_name(name: str) -> bool:
    marker = name.rfind("__")
    if marker <= 0 or not name.endswith(".json"):
        return False
    suffix = name[marker + 2 : -5]
    return len(suffix) == 16 and all(char in _HEX for char in suffix)


def _prune_empty(root: Path) -> None:
    for directory, _dirnames, _filenames in os.walk(root, topdown=False):
        current = Path(directory)
        if current == root:
            continue
        if not any(current.iterdir()):
            current.rmdir()
