# LlamaParse adapter fixtures

These are LlamaParse v2 `GET /api/v2/parse/{job_id}?expand=items,markdown,job_metadata` bodies.

The item shapes follow the published response format (https://developers.llamaindex.ai/llamaparse/parse/guides/response-format/, read 2026-11-02). Job ids are synthetic. There are no API keys or personal data. They were composed from that schema because this environment has no `LLAMA_CLOUD_API_KEY` to record a live job.

- `agentic/invoice` — one page: header, heading, paragraph, table, list, footer. `job.version` is `2026-09-29`.
- `fast/edges` — normalized and union boxes, an HTML rowspan, a failed second page, and the other item types. `raw_parameters.version` is `2026-06-15`.

`*.doc.json` is the Document `to_document` must produce for the parser version named above.
