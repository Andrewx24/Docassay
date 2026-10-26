---
title: Verify (runtime)
sidebar_position: 10
---

# Verify

`verify()` checks, in production, that each extracted value actually appears in the source span it cites. It needs no golden set. Built in **ALI-329**.

```python
from docassay import verify

result = verify(doc, {
    "total":    {"value": 4120.00, "citations": [{"page": 2, "blockId": "t1"}]},
    "due_date": {"value": "2026-11-01", "citations": [{"page": 1, "blockId": "b7"}]},
})
result.ok           # False
result.unverified   # [FieldCheck(name="total", status="value_not_found", ...)]
```

```ts
import {verify} from 'docassay/verify';   // separate entry point, no heavy deps
const result = verify(doc, fields);
```

## Algorithm

For each field, in input order:

1. **No citations** → status `uncited`.
2. For each citation, resolve the **span text**:
   - `blockId` names a block → that block's text. If `block.page != citation.page` → status `citation_invalid`.
   - `blockId` names a table → all cells of that table, joined with spaces.
   - Only `bbox` given → text of every block and table on that page whose bbox overlaps it (any positive intersection area), in array order.
   - Only `page` given → whole page text; the check is marked `weak: true`.
   - Page does not exist → `citation_invalid`.
3. **Find the value in the span:**
   - Number → extract every number-like token from the span (`[\(\-]?[$€£]?\d[\d.,' ]*\d?%?\)?`), parse each with `parse_number`, and pass if any is numerically equal.
   - Date → slide over the span and pass if any substring that `parse_date` accepts equals the value.
   - Boolean → skip (always `verified`, marked `weak`).
   - String → pass if `norm_text(value)` is a substring of `norm_text(span)`.
4. The field is `verified` if **any** citation passes; otherwise `value_not_found`.

```ts
interface VerifyResult {
  ok: boolean;                     // every field verified
  checks: FieldCheck[];
  unverified: FieldCheck[];        // convenience: checks with status != "verified"
}
interface FieldCheck {
  name: string;
  status: "verified" | "value_not_found" | "citation_invalid" | "uncited";
  weak?: boolean;
  citation?: Citation;             // the one that passed, or the first one tried
  reason?: string;
}
```

## Constraints

- Imports only the models and normalization modules: no YAML, CLI or adapter code. In TypeScript it is exported from a separate subpath (`docassay/verify`) so bundlers can tree-shake it into a web service.
- Never raises on malformed input; returns `citation_invalid` with a reason instead.
- Under 1 ms per field for typical documents. No regex compiled per call.
