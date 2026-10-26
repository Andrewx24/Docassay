---
title: Adapters
sidebar_position: 7
---

# Adapters

An adapter runs one parser and converts its native output into a `Document`. Adapters are the only place vendor-specific code lives.

## Interface

Every adapter is split into **fetch** (talks to the parser) and **map** (pure conversion). Mapping is where the bugs are, and splitting it out means it can be tested against recorded responses with no network or API key.

```python
class Adapter(Protocol):
    id: ClassVar[str]                    # "llamaparse"
    variants: ClassVar[tuple[str, ...]]  # ("fast", "cost_effective", "agentic", "agentic_plus") or ()

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None: ...
    def available(self) -> tuple[bool, str]:           # (ok, reason) e.g. missing API key or extra
    def cache_identity(self) -> dict[str, Any]:        # variant + options that change output
    def fetch(self, path: Path) -> RawResult:           # network / library call
    def to_document(self, raw: RawResult, source: Source) -> Document:  # pure
```

```ts
interface Adapter {
  readonly id: string;
  readonly variants: readonly string[];
  available(): Promise<{ok: boolean; reason?: string}>;
  cacheIdentity(): Record<string, unknown>;
  fetch(path: string): Promise<RawResult>;
  toDocument(raw: RawResult, source: Source): Document;   // pure
}

interface RawResult {
  payload: unknown;          // vendor JSON, stored in test fixtures
  parserVersion: string;     // what the vendor reports actually ran
  latencyMs: number;
  costUsd?: number;
}
```

The runner, not the adapter, fills `source` (path, sha256, mime) and `meta.latencyMs`, `meta.costUsd`, `meta.adapterVersion`.

## Ids and variants

`id[:variant]` — for example `docling`, `llamaparse:agentic`, `reducto`, `file`. An unknown variant is a config error (exit code 2), not a runtime error.

## Registry

- **Python:** built-in adapters register in `docassay.adapters.registry`. Third-party adapters register through the entry-point group `docassay.adapters`.
- **TypeScript:** built-ins are registered in `src/adapters/registry.ts`; third parties call `registerAdapter(factory)` before `run()`.
- Vendor SDKs are **lazy-imported** inside `fetch`, so the core never requires them.

## Built-in adapters (v0.1)

| Id | Issue | Python | TypeScript | Credentials / setup |
| --- | --- | --- | --- | --- |
| `file` | ALI-342 | yes | yes | none |
| `docling` | ALI-323 | library (`docassay[docling]`) | HTTP to `docling-serve` | `DOCLING_BASE_URL` for HTTP mode |
| `llamaparse:<tier>` | ALI-324 | yes | yes | `LLAMA_CLOUD_API_KEY` |
| `reducto` | ALI-325 | yes | yes | `REDUCTO_API_KEY` |

Vendor APIs change often. Whoever implements an adapter must read the vendor's **current** API docs, record the exact API version used in `parserVersion`, and save at least two real responses as fixtures.

### `file` adapter

Scores output a user's own pipeline already produced. It is the first adapter built because it needs no network and lets the whole scoring path be tested end to end.

```yaml
parsers:
  - id: file
    name: my-pipeline
    options:
      dir: out/parsed        # looks for <dir>/<golden id>.json, then <dir>/<golden id>.md
```

- `.json` must validate against `document.schema.json` and is used as-is.
- `.md` is converted: ATX headings → `heading` blocks with `level`, paragraphs → `paragraph`, list items → `list_item`, GFM pipe tables → `Table` (first row = header). Everything gets `page: 1` unless the Markdown contains `<!-- page: N -->` markers.

## Mapping rules every adapter follows

- Block types are mapped to the spec enum; anything unrecognized becomes `other`. Never drop text.
- Page headers and footers keep their own types (`header`, `footer`) so scorers and users can decide what to do with them.
- Bounding boxes are converted to top-left-origin PDF points. If the vendor gives normalized 0–1 coordinates, multiply by page size; if page size is unknown, omit `bbox`.
- Table cells: merged cells are repeated into every cell they span; empty cells are `""`; ragged rows are padded.
- If the vendor returns chunks, map them with `blockIds`; otherwise leave `chunks` absent.
- Errors: missing credentials or extras → `AdapterUnavailable` (checked via `available()` before a run starts); vendor or network failure → `AdapterError` with the vendor's message. The runner records the error on that document and continues.

## Testing

```text
python/tests/adapters/fixtures/llamaparse/agentic/invoice-acme.raw.json   # recorded payload
python/tests/adapters/fixtures/llamaparse/agentic/invoice-acme.doc.json   # expected Document
```

Unit tests call `to_document(raw)` and compare to the expected Document. Live tests that call the vendor are marked (`@pytest.mark.live`, `describe.skipIf(!process.env.LIVE)`) and run only in the nightly workflow.
