---
title: Normalization
sidebar_position: 6
---

# Normalization

Every comparison in docassay goes through three functions. They are the most parity-sensitive code in the project, because a one-character difference between Python and TypeScript becomes a different score. Built in **ALI-339**.

| Function | Python | TypeScript |
| --- | --- | --- |
| Text | `norm_text(s) -> str` | `normText(s): string` |
| Number | `parse_number(s, decimal=".") -> float \| None` | `parseNumber(s, decimal?): number \| null` |
| Date | `parse_date(s, order="mdy") -> str \| None` (ISO `YYYY-MM-DD`) | `parseDate(s, order?): string \| null` |
| Comparison | `values_equal(expected, actual, opts) -> bool` | `valuesEqual(expected, actual, opts): boolean` |

## `norm_text`

Apply these steps **in this order**:

1. Unicode **NFKC** (folds ligatures such as `ﬁ` → `fi`, full-width characters, superscripts).
2. Delete U+00AD (soft hyphen), U+200B, U+200C, U+200D, U+FEFF.
3. Map quotes: `‘ ’ ‚ ‛ ′` → `'` and `“ ” „ ‟ ″` → `"`.
4. Map dashes: U+2010–U+2015 and U+2212 → `-`.
5. **De-hyphenate line breaks:** remove `-` + optional spaces/tabs + `\n` + optional whitespace when the character before the `-` and the character after are both Unicode letters (general category `L*`). `"conti-\n nued"` → `"continued"`.
6. **Collapse whitespace:** every maximal run of characters from the set below becomes one ASCII space.
7. Trim leading and trailing spaces.
8. Lowercase: Python `str.lower()`, TypeScript `String.prototype.toLowerCase()`.

**Whitespace set** (do not use `\s` or `str.isspace`, which differ between languages):

```text
U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A,
U+2028, U+2029, U+202F, U+205F, U+3000
```

## `parse_number`

Returns a float or `None`. Steps:

1. `t = norm_text(s)` with step 8 (lowercasing) skipped, then remove every whitespace character and `'`.
2. Strip a leading or trailing currency marker: one of `$ € £ ¥ ₹ ₩ ₽ ¢` or a 3-letter uppercase ISO code (`USD`, `EUR`, …) with optional surrounding spaces.
3. Strip a trailing `%` (the number is returned as written: `"12%"` → `12`).
4. Negatives: `(x)` → `-x`; trailing `-` → leading `-`.
5. Decimal handling: with `decimal="."` remove every `,`; with `decimal=","` remove every `.` then replace `,` with `.`.
6. The result must fully match `^-?\d+(\.\d+)?$`, otherwise return `None`.

| Input | `decimal="."` | `decimal=","` |
| --- | --- | --- |
| `"1,200.00"` | 1200 | None |
| `"$ 4,120.00"` | 4120 | None |
| `"1.200,00 €"` | None | 1200 |
| `"(350)"` | -350 | -350 |
| `"12%"` | 12 | 12 |
| `"N/A"` | None | None |

Numeric equality: `abs(a - b) <= 1e-6 * max(1, abs(b))`.

## `parse_date`

Returns ISO `YYYY-MM-DD` or `None`. Accepted forms (after `norm_text`):

| Form | Example |
| --- | --- |
| ISO | `2026-11-01`, `2026/11/01` |
| Numeric with 4-digit year | `11/01/2026`, `01.11.2026`, `1-11-2026` (interpreted by `date_order`: `mdy` or `dmy`) |
| Month name | `November 1, 2026`, `Nov 1 2026`, `1 November 2026`, `1 Nov. 2026` (English names, full or 3-letter) |

Two-digit years are **not** parsed. Invalid calendar dates (`2026-02-30`) return `None`. Parsing never depends on the system locale or time zone.

## `values_equal(expected, actual)`

1. If both parse as numbers → numeric equality.
2. Else if both parse as dates → equal ISO strings.
3. Else → `norm_text(expected) == norm_text(actual)`.

A number and a date are never equal to each other.

## YAML loading

Golden files and config must be parsed with **YAML 1.2 core schema** semantics in both languages:

- Python: a `yaml.SafeLoader` subclass with the timestamp implicit resolver removed (or `ruamel.yaml` in 1.2 mode), so `2026-11-01` stays a string.
- TypeScript: the `yaml` package (v2), default 1.2 core schema.

The parity suite includes a golden file with unquoted dates, `yes`/`no`, and `0o17`-style values to lock this in.
