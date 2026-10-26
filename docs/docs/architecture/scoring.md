---
title: Scoring
sidebar_position: 5
---

# Scoring

Every scorer is a pure function with the same shape in both languages:

```python
def score_tables(doc: Document, expect: list[ExpectedTable], opts: ScoreOptions) -> MetricResult: ...
```

```ts
function scoreTables(doc: Document, expect: ExpectedTable[], opts: ScoreOptions): MetricResult;
```

```ts
interface MetricResult { score: number; failures: Failure[] }  // score in [0, 1]
```

Rules that apply to every scorer:

- Deterministic: no network, randomness, clock or environment reads.
- Ties are broken by **array order** (first wins). Never by hash or dictionary order.
- All string comparisons go through [normalization](normalization.md).
- A score below 1 must come with at least one `Failure`.
- Compute in float64; round to 4 decimals only when writing output.

## Tables {#tables}

For each expected table `E`, in golden-file order:

**1. Candidates.** Predicted tables with `page == E.page` that have not already been matched. If `E.page` is omitted, all unmatched tables are candidates. No candidates → score 0, failure `"no table found on page N"`.

**2. Effective header.** For a predicted table `T`, the effective header is `T.header` if present, otherwise `T.rows[0]` with the body being `T.rows[1:]`.

**3. Header similarity.**

```text
headerSim(E, T) = |multiset intersection of norm(E.header), norm(effHeader(T))|
                  / max(len(E.header), len(effHeader(T)))
```

**4. Match.** Pick the candidate with the highest `headerSim` (first wins on ties). If `E.header` is given and the best `headerSim < 0.5`, the table is unmatched: score 0, failure `"no table with matching header"`. If `E.header` is not given, pick the candidate with the highest cell score instead. A matched table is removed from later candidate lists.

**5. Components.** Each is in `[0, 1]`; only components with labels are used.

| Component | Present when | Value |
| --- | --- | --- |
| header | `E.header` | `headerSim` |
| shape | `E.rows` and/or `E.cols` | mean of `1 − min(1, |actual − expected| / expected)` for each given dimension. Actual cols = length of effective header, or longest body row if there is none. |
| cells | `E.cells` | fraction of cells where `valuesEqual(expected, actual)` |

Cell lookup: `row` indexes the **body** rows (0-based). `col` is an integer index, or a header name looked up first in the predicted effective header, then in `E.header` as a fallback. A missing row or column counts as a failed cell with reason `"cell out of range"`.

**6. Combine.**

```text
tableScore = (0.3·header + 0.2·shape + 0.5·cells) / (sum of weights of components present)
```

**7. Document score** = mean of `tableScore` over all expected tables.

:::note v0.2
TEDS (tree edit distance on HTML tables) is planned as an additional `structure` component when a golden file labels a full table. It is out of scope for v0.1 because a parity-safe implementation in both languages is non-trivial.
:::

## Text {#text}

```text
score = (number of strings s in text_contains where norm(s) is a substring of norm(documentText(doc)))
        / len(text_contains)
```

Matching is exact after normalization, which already handles case, whitespace, line-break hyphenation, ligatures, quote and dash variants. There is no fuzzy matching in v0.1, so results are explainable and identical in both languages. Failure reason: `"text not found"`, with the expected string.

## Reading order {#reading-order}

Let `T = norm(documentText(doc))`. For each pair `[a, b]`:

1. `i` = index of the first occurrence of `norm(a)` in `T`. Not found → fail, `"first string not found"`.
2. Pass if `norm(b)` occurs anywhere in `T` starting at `i + len(norm(a))`. Otherwise, if `norm(b)` is not in `T` at all → `"second string not found"`, else → `"out of order"`.

`score = passed / len(reading_order)`.

## Fields {#fields}

If `doc.fields` is absent, every expected field fails with `"parser returned no fields"`.

For each `key: expected` in golden order, let `actual = doc.fields[key].value` (missing key → fail `"field missing"`). Compare with:

| Expected (YAML) | Rule |
| --- | --- |
| `null` | pass if actual is `null`, missing or `norm(actual) == ""` |
| `true` / `false` | pass if actual is the same boolean, or the string `"true"`/`"false"`/`"yes"`/`"no"` mapping to it |
| number | `valuesEqual` numeric rule |
| string | `valuesEqual` (number, then date, then text rule) |

`score = passed / number of expected fields`.

:::warning YAML dates
YAML 1.1 turns `2026-11-01` into a date object in PyYAML but leaves it a string in the JS `yaml` package. Golden files **must be loaded with YAML 1.2 core-schema semantics in both languages**, so unquoted dates stay strings. The Python loader must not construct timestamps. See [Normalization](normalization.md).
:::

## Chunks {#chunks}

If `doc.chunks` is absent, the metric scores 0 with `"parser produced no chunks"`.

Each rule produces a list of checks; `score = passed checks / total checks`.

| Rule | Checks |
| --- | --- |
| `max_tokens: N` | one per chunk: `tokens(chunk) ≤ N` |
| `min_tokens: N` | one per chunk except the last chunk of the document: `tokens(chunk) ≥ N` |
| `no_split_tables: true` | one per table in `doc.tables`: pass if at most one chunk lists the table id in `blockIds`, or every such chunk contains every normalized effective-header cell of that table |
| `heading_paths[]` | one per item: the first chunk whose normalized text contains `norm(item.text)` must have `headingPath` equal (element-wise, normalized) to `item.path`. No such chunk → fail `"text not in any chunk"` |

`tokens(chunk)` is `chunk.tokens` if set, else the configured tokenizer. The default tokenizer `words` counts maximal runs of non-whitespace characters after normalization (whitespace set defined in [Normalization](normalization.md)). `tiktoken:<encoding>` is an optional extra and is excluded from parity tests.

## Aggregation

```text
doc.overall      = mean of the metric scores present for that document
summary[metric]  = mean of doc.scores[metric] over documents that have that metric
summary.overall  = mean of doc.overall over all documents
```

A document whose adapter failed gets `error` set and **0 for every metric its golden file defines**. Failing loudly beats silently shrinking the denominator.

Cost and latency are reported next to quality but never mixed into any score.
