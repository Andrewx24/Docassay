---
title: "ADR 0001: One monorepo"
---

# ADR 0001: One monorepo for spec, both packages and docs

**Status:** Accepted · 2026-10-26

## Context

docassay ships a Python package and a TypeScript package that must behave identically. Both depend on a shared spec and a shared fixture corpus.

## Decision

Keep `spec/`, `python/`, `js/`, `golden/` and `docs/` in one repository (`Andrewx24/Docassay`). A spec change and both implementations land in one pull request, and CI runs the parity job across both.

## Consequences

- One issue tracker, one release tag, one changelog.
- CI must be path-aware so a docs-only change does not run the full matrix.
- Contributors who only know one language will touch the other's CI; `AGENTS.md` and `CONTRIBUTING.md` explain how to run both locally.
