---
title: "ADR 0005: License"
---

# ADR 0005: Apache-2.0

**Status:** Proposed · 2026-10-26 · owner: Andrew Aliaj

## Context

docassay should be easy for companies to adopt. The main candidates are MIT and Apache-2.0.

## Decision

Apache-2.0: permissive like MIT, plus an explicit patent grant that legal teams at larger companies often prefer.

## Consequences

- Contributions are accepted under Apache-2.0 (inbound = outbound); no CLA for now.
- Bundled or optional dependencies must be Apache-compatible. Docling (MIT) is fine. Check every adapter's SDK license before adding it as an extra.
- To switch to MIT, change this ADR to "Superseded" and replace `LICENSE` before v0.1.0.
