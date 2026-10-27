## Summary

ALI-NNN:

## Definition of done

- [ ] Title starts with the Linear id (`ALI-NNN: …`)
- [ ] Scope matches one issue; every acceptance criterion in the issue is met
- [ ] CI is green (lint, typecheck, tests, spec drift, parity, docs build — whichever exist)
- [ ] No hand edits in generated `models/` folders
- [ ] New public functions have a docstring / TSDoc with one example

### Code in `python/` or `js/`

- [ ] Implemented in both languages if the issue is labeled with both
- [ ] Same module names and function names (snake_case ↔ camelCase)
- [ ] Line coverage of new code ≥ 90%
- [ ] No network calls in unit tests

### Spec changed?

- [ ] No schema change in this PR
- [ ] Yes: `spec/*.schema.json`, `spec/examples/`, the Spec doc page, and both generated model sets are updated together
- [ ] `specVersion` bumped if the change is breaking

### Parity fixtures added?

- [ ] Not a scoring, normalization, or verify change
- [ ] Yes: fixtures under `spec/fixtures/` cover the new behavior and at least one failure case
- [ ] Doc page updated if behavior changed (Scoring, Normalization, or Verify)
- [ ] Parity passes to 4 decimal places

### Adapters

- [ ] Not an adapter change
- [ ] At least two recorded vendor payloads with expected `Document` outputs
- [ ] `available()` returns a clear reason when credentials or extras are missing
- [ ] `parserVersion` records the vendor model or version used
- [ ] Payloads scrubbed of keys and personal data

## Test plan

- [ ]
