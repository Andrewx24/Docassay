"""Number parsing. See the Normalization doc for the step order.

Example:
    parse_number("(350)") == -350
"""

from __future__ import annotations

from typing import Literal

from docassay.normalize.text import is_whitespace, norm_text

_CURRENCY_SYMBOLS = frozenset("$€£¥₹₩₽¢")

# ISO 4217 alphabetic codes used as leading or trailing markers.
_ISO_BLOCK = """
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN
BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK
DJF DKK DOP DZD
EGP ERN ETB EUR
FJD FKP
GBP GEL GHS GIP GMD GNF GTQ GYD
HKD HNL HTG HUF
IDR ILS INR IQD IRR ISK
JMD JOD JPY
KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD
MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN
NAD NGN NIO NOK NPR NZD
OMR
PAB PEN PGK PHP PKR PLN PYG
QAR
RON RSD RUB RWF
SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD STN SVC SYP SZL
THB TJS TMT TND TOP TRY TTD TWD TZS
UAH UGX USD UYU UZS
VES VND VUV
WST
XAF XCD XOF XPF
YER
ZAR ZMW ZWL
"""


def _iso_codes(block: str) -> frozenset[str]:
    found: list[str] = []
    for line in block.split("\n"):
        for word in line.split(" "):
            if word:
                found.append(word)
    return frozenset(found)


_ISO_CODES = _iso_codes(_ISO_BLOCK)


def _strip_end(text: str, *, leading: bool) -> str:
    if not text:
        return text
    if leading:
        if text[0] in _CURRENCY_SYMBOLS:
            return text[1:]
        if len(text) >= 3 and text[:3] in _ISO_CODES:
            return text[3:]
        return text
    if text[-1] in _CURRENCY_SYMBOLS:
        return text[:-1]
    if len(text) >= 3 and text[-3:] in _ISO_CODES:
        return text[:-3]
    return text


def _apply_sign(text: str) -> str:
    if len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = "-" + text[1:-1]
    if text.endswith("-"):
        body = text[:-1]
        if body.startswith("-"):
            return body
        return "-" + body
    return text


def _apply_decimal(text: str, decimal: Literal[".", ","]) -> str | None:
    """Apply the decimal mark.

    When both separators appear, the rightmost one is the decimal mark. If it
    is not `decimal`, the input is rejected. That is what makes `1,200.00`
    parse for `decimal="."` and fail for `decimal=","`.
    """
    last_dot = text.rfind(".")
    last_comma = text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        mark = "." if last_dot > last_comma else ","
        if mark != decimal:
            return None
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "")
        if decimal == ",":
            text = text.replace(",", ".")
        return text
    if decimal == ".":
        return text.replace(",", "")
    return text.replace(".", "").replace(",", ".")


def _is_plain_number(text: str) -> bool:
    """Match the ASCII form ^-?[0-9]+(\\.[0-9]+)?$ without a digit class."""
    if not text:
        return False
    index = 0
    if text[0] == "-":
        index = 1
        if index == len(text):
            return False
    saw_dot = False
    digits = 0
    fraction = 0
    while index < len(text):
        ch = text[index]
        if ch == ".":
            if saw_dot or digits == 0:
                return False
            saw_dot = True
        elif "0" <= ch <= "9":
            if saw_dot:
                fraction += 1
            else:
                digits += 1
        else:
            return False
        index += 1
    return not saw_dot or fraction > 0


def parse_number(text: str, decimal: Literal[".", ","] = ".") -> float | None:
    """Parse a number, or return None when the text is not a number.

    Example:
        parse_number("1.200,00 €", decimal=",") == 1200
    """
    cleaned = norm_text(text, lower=False)
    cleaned = "".join(ch for ch in cleaned if not is_whitespace(ch) and ch != "'")
    cleaned = _strip_end(cleaned, leading=True)
    cleaned = _strip_end(cleaned, leading=False)
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    cleaned = _apply_sign(cleaned)
    applied = _apply_decimal(cleaned, decimal)
    if applied is None or not _is_plain_number(applied):
        return None
    return float(applied)
