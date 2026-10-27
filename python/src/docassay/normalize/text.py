"""Text normalization. Steps run in the order defined in the Normalization doc.

Example:
    norm_text("conti-\\n nued") == "continued"
"""

from __future__ import annotations

import unicodedata

# Characters removed after NFKC. Not part of the whitespace set.
_DELETED = frozenset("\u00ad\u200b\u200c\u200d\ufeff")

_SINGLE_QUOTES = frozenset("\u2018\u2019\u201a\u201b\u2032")
_DOUBLE_QUOTES = frozenset("\u201c\u201d\u201e\u201f\u2033")

# Spaces and tabs only. Used by line-break dehyphenation, not by collapsing.
_SPACE_OR_TAB = frozenset(" \t")


def is_whitespace(ch: str) -> bool:
    """Return whether `ch` is in the explicit whitespace set.

    The set is U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A,
    U+2028, U+2029, U+202F, U+205F, and U+3000.

    Example:
        is_whitespace("\\u00a0") is True
    """
    code = ord(ch)
    return (
        0x0009 <= code <= 0x000D
        or code == 0x0020
        or code == 0x0085
        or code == 0x00A0
        or code == 0x1680
        or 0x2000 <= code <= 0x200A
        or code in (0x2028, 0x2029, 0x202F, 0x205F, 0x3000)
    )


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def _is_dash(ch: str) -> bool:
    code = ord(ch)
    return 0x2010 <= code <= 0x2015 or code == 0x2212


def _dehyphenate(text: str) -> str:
    """Join a line-break hyphen when both sides are letters."""
    out: list[str] = []
    index = 0
    size = len(text)
    while index < size:
        if text[index] == "-" and out and _is_letter(out[-1]):
            cursor = index + 1
            while cursor < size and text[cursor] in _SPACE_OR_TAB:
                cursor += 1
            if cursor < size and text[cursor] == "\n":
                cursor += 1
                while cursor < size and is_whitespace(text[cursor]):
                    cursor += 1
                if cursor < size and _is_letter(text[cursor]):
                    index = cursor
                    continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _collapse_whitespace(text: str) -> str:
    """Turn each whitespace run into one space, then trim those spaces."""
    out: list[str] = []
    in_run = False
    for ch in text:
        if is_whitespace(ch):
            in_run = True
            continue
        if in_run:
            out.append(" ")
            in_run = False
        out.append(ch)
    if in_run:
        out.append(" ")
    collapsed = "".join(out)
    start = 0
    end = len(collapsed)
    while start < end and collapsed[start] == " ":
        start += 1
    while end > start and collapsed[end - 1] == " ":
        end -= 1
    return collapsed[start:end]


def norm_text(text: str, *, lower: bool = True) -> str:
    """Normalize text before comparison.

    `lower=False` skips the final lowercasing step. `parse_number` uses that
    so currency codes stay uppercase.

    Example:
        norm_text("conti-\\n nued") == "continued"
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(ch for ch in normalized if ch not in _DELETED)
    quoted: list[str] = []
    for ch in normalized:
        if ch in _SINGLE_QUOTES:
            quoted.append("'")
        elif ch in _DOUBLE_QUOTES:
            quoted.append('"')
        elif _is_dash(ch):
            quoted.append("-")
        else:
            quoted.append(ch)
    collapsed = _collapse_whitespace(_dehyphenate("".join(quoted)))
    if lower:
        return collapsed.lower()
    return collapsed
