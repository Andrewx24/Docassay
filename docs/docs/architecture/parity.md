---
title: Cross-language parity
sidebar_position: 11
---

# Cross-language parity

The Python and TypeScript packages must give the same score for the same input. Users will mix them (a Python RAG service, a TypeScript CI script), and two answers would destroy trust in both. Built in **ALI-321**.

## Three layers of protection

| Layer | How | Catches |
| --- | --- | --- |
| Generated types | `scripts/gen-types.sh` builds both model sets from `spec/`; CI fails on diff | A field added in one language only |
| Shared fixtures | `spec/fixtures/` is loaded by both test suites | Scoring and normalization differences |
| Parity job | `scripts/parity.sh` runs both CLIs on the fixtures with `--format json` and diffs | Differences in aggregation, reporting, CLI behavior |

## Fixture format

```text
spec/fixtures/
  tables-basic/
    document.json        # a Document (already parsed; no adapter involved)
    golden.yaml
    expected.json        # {"scores": {...}, "failures": [...]} for this one document
  normalize/
    text.json            # [{"input": "conti-\n nued", "expected": "continued"}, ...]
    numbers.json         # [{"input": "(350)", "decimal": ".", "expected": -350}, ...]
    dates.json
  verify/
    cases.json
```

Both test suites iterate over every folder. Scores must match `expected.json` to **4 decimal places**, and failures must match on `metric`, `check` and `reason`.

## Rules for writing parity-safe code

- Do not use regex classes whose meaning differs: `\s`, `\w`, `\b`, `\d` (in Python `\d` matches non-ASCII digits). Use explicit character sets from [Normalization](normalization.md).
- Do not rely on dictionary or object key order from parsed input; iterate in the order the spec defines (golden-file order, array order).
- Do not use locale-aware functions (`toLocaleString`, `locale.atof`, `strftime` with names).
- Sort with explicit comparators. JavaScript's default sort is lexicographic on strings.
- Floats: compute in float64 and round **only when writing output**, using one shared helper defined as `round4(x) = floor(x * 10000 + 0.5) / 10000` in both languages. Do not use Python's built-in `round()` (it rounds half to even) or `toFixed()` (string rounding quirks). Tests compare with a tolerance of `1e-4`.
- Every new metric or normalization rule needs fixtures in the same pull request.
