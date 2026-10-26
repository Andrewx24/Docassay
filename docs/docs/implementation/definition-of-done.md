---
title: Definition of done
sidebar_position: 3
---

# Definition of done

A pull request is mergeable when every item that applies is true.

## Every PR

- [ ] Title starts with the Linear id: `ALI-339: …`
- [ ] Scope matches one issue; acceptance criteria in the issue are all met
- [ ] CI is green: lint, typecheck, tests, spec drift, parity, docs build
- [ ] No hand edits in generated `models/` folders
- [ ] New public functions have docstrings / TSDoc with one example

## Code in `python/` or `js/`

- [ ] Implemented in **both** languages if the issue is labeled with both
- [ ] Same module names and function names (snake_case ↔ camelCase)
- [ ] Line coverage of new code ≥ 90% (`pytest --cov`, `vitest --coverage`)
- [ ] No network calls in unit tests

## Scoring, normalization or verify changes

- [ ] Fixtures added under `spec/fixtures/` covering the new behavior and at least one failure case
- [ ] Doc page updated if behavior changed ([Scoring](../architecture/scoring.md), [Normalization](../architecture/normalization.md), [Verify](../architecture/verify.md))
- [ ] Parity job passes to 4 decimal places

## Schema changes

- [ ] `spec/*.schema.json`, `spec/examples/` and the [Spec](../architecture/spec.md) page all updated
- [ ] Types regenerated and committed in both packages
- [ ] `specVersion` bumped if the change is breaking

## Adapters

- [ ] At least two recorded vendor payloads with expected `Document` outputs
- [ ] `available()` returns a clear reason when credentials or extras are missing
- [ ] `parserVersion` records the exact vendor model/version used
- [ ] Payloads scrubbed of keys and personal data
