---
title: "ADR 0002: Spec first"
---

# ADR 0002: JSON Schema is the source of truth

**Status:** Accepted · 2026-10-26

## Context

Two hand-maintained model sets drift. The data model is also the public contract for third-party adapters and for anyone reading `--format json`.

## Decision

Every data shape is defined in `spec/*.schema.json` (JSON Schema draft 2020-12). Python models (Pydantic v2, via `datamodel-code-generator`) and TypeScript types (via `json-schema-to-typescript`) are generated, committed, and checked for drift in CI. Runtime validation of user input (golden files, config, `file`-adapter JSON) uses the same schemas (`jsonschema` in Python, `ajv` in TypeScript).

## Consequences

- Changing a field means editing a schema first, then regenerating.
- Generated code may be less idiomatic; thin hand-written helpers can wrap it, but never edit generated files.
- The spec gets its own semver (`specVersion`).
