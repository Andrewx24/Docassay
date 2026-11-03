# Docling adapter fixtures

`digital-table.raw.json` and `scanned-page.raw.json` are DoclingDocument 1.10.0 exports from the Docling test suite (MIT), copied unchanged:

- `tests/data/pdf/groundtruth/2305.03393v1-pg9.json` — one digital PDF page with a table, page headers, and a caption
- `tests/data/ocr/groundtruth/tesseract/full_page/ocr_test.tesseract.full_page.json` — one scanned PDF page

`spans.raw.json` and `edges.raw.json` are small constructed documents in the same shape. They lock colspan repetition, normalized boxes, and a bottom-left box on a page with no known size.

`*.doc.json` is the Document `to_document` must produce for parser version `2.87.0`.
