---
title: "ADR 0003: Two native packages"
---

# ADR 0003: Two native packages instead of one core with bindings

**Status:** Accepted · 2026-10-26

## Context

Options considered: (a) native Python and TypeScript packages; (b) a Python core with an npm wrapper that shells out; (c) a Rust core with bindings.

## Decision

(a) Two native packages. docassay's core is small (normalization, five scorers, aggregation, gate), so writing it twice is cheaper than maintaining a cross-language build. Users install with one command and need no second runtime.

## Consequences

- Scoring logic exists twice; parity fixtures are mandatory (see [Parity](../architecture/parity.md)).
- Adapters may differ in transport (Docling is a library in Python but HTTP in TypeScript), but never in mapping output.
- If the core grows large (for example TEDS or learned metrics), revisit (c).
