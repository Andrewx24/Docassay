"""Date parsing to ISO YYYY-MM-DD. No locale and no timezone.

Example:
    parse_date("Nov. 1 2026") == "2026-11-01"
"""

from __future__ import annotations

from typing import Literal

from docassay.normalize.text import norm_text

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

DateOrder = Literal["mdy", "dmy"]


def _digits(text: str) -> bool:
    return bool(text) and all("0" <= ch <= "9" for ch in text)


def _year(part: str) -> int | None:
    if len(part) != 4 or not _digits(part):
        return None
    return int(part)


def _day_or_month(part: str) -> int | None:
    if not _digits(part) or len(part) > 2:
        return None
    return int(part)


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _ymd(year: int, month: int, day: int) -> str | None:
    if not 1 <= month <= 12:
        return None
    if month == 2:
        last = 29 if _leap(year) else 28
    elif month in (4, 6, 9, 11):
        last = 30
    else:
        last = 31
    if not 1 <= day <= last:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _split3(text: str, separators: str) -> list[str] | None:
    used = [sep for sep in separators if sep in text]
    if len(used) != 1:
        return None
    parts = text.split(used[0])
    if len(parts) != 3 or any(part == "" for part in parts):
        return None
    return parts


def _parse_iso(text: str) -> str | None:
    parts = _split3(text, "-/")
    if parts is None:
        return None
    year = _year(parts[0])
    month = _day_or_month(parts[1])
    day = _day_or_month(parts[2])
    if year is None or month is None or day is None:
        return None
    return _ymd(year, month, day)


def _parse_numeric(text: str, order: DateOrder) -> str | None:
    parts = _split3(text, "-/.")
    if parts is None:
        return None
    year = _year(parts[2])
    first = _day_or_month(parts[0])
    second = _day_or_month(parts[1])
    if year is None or first is None or second is None:
        return None
    if order == "dmy":
        day, month = first, second
    else:
        month, day = first, second
    return _ymd(year, month, day)


def _month_token(token: str) -> int | None:
    name = token[:-1] if token.endswith(".") else token
    if token.endswith(".") and len(name) != 3:
        return None
    return _MONTHS.get(name)


def _parse_month_name(text: str) -> str | None:
    if not text:
        return None
    tokens = [token[:-1] if token.endswith(",") else token for token in text.split(" ")]
    if len(tokens) != 3:
        return None
    year = _year(tokens[2])
    if year is None:
        return None
    month = _month_token(tokens[0])
    if month is not None:
        day = _day_or_month(tokens[1])
        if day is None:
            return None
        return _ymd(year, month, day)
    day = _day_or_month(tokens[0])
    month = _month_token(tokens[1])
    if day is None or month is None:
        return None
    return _ymd(year, month, day)


def parse_date(text: str, order: DateOrder = "mdy") -> str | None:
    """Parse a date to ISO YYYY-MM-DD, or return None.

    `order` is `mdy` or `dmy` and applies only to numeric dates whose year is
    last. Two-digit years and invalid calendar dates return None.

    Example:
        parse_date("Nov. 1 2026") == "2026-11-01"
    """
    normalized = norm_text(text)
    iso = _parse_iso(normalized)
    if iso is not None:
        return iso
    numeric = _parse_numeric(normalized, order)
    if numeric is not None:
        return numeric
    return _parse_month_name(normalized)
