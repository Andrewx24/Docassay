# Reducto adapter fixtures

These payloads follow the published parse and extract response format (https://docs.reducto.ai/parse/response-format and https://docs.reducto.ai/extract/response-format, read 2026-11-02). Job ids are synthetic. There is no API key and no personal data. They were composed from that schema because this environment has no `REDUCTO_API_KEY` to record a live job.

Reducto boxes are normalized 0–1 and the vendor response has no page size. The `pages` array is added by the adapter so mapping can scale boxes to PDF points. A box is omitted when its page size is missing.

- `parse/invoice` — title, key-value, section header, markdown table, list item, header, footer, figure, and a second chunk. Page 1 is 612×792.
- `parse/edges` — an HTML rowspan, a ragged markdown table, an unknown block type, a blank text block, and a second page with no size so its 0–1 box is omitted.
- `extract/invoice` — scalar fields whose citations resolve a `blockId` and a scaled box.
- `extract/nested` — a nested object, an array of objects, a boolean, a JSON object value, and a citation whose page has no size.

`*.doc.json` is the Document `to_document` must produce for parser version `r-1`.
