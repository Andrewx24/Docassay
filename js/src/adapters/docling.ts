/**
 * Docling adapter.
 *
 * TypeScript posts the file to docling-serve (`DOCLING_BASE_URL`). Mapping is
 * pure and matches the Python extra, which calls the `docling` library.
 * `parserVersion` is the server version when the response includes one, and
 * otherwise `docling-document-` plus the export's schema version.
 *
 * @example
 * ```ts
 * import { DoclingAdapter } from "./adapters/docling.js";
 *
 * const adapter = new DoclingAdapter(null, {});
 * const check = await adapter.available();
 * ```
 */

import { readFile } from "node:fs/promises";
import path from "node:path";

import { AdapterError, type Adapter, type RawResult } from "./base.js";
import { packageVersion } from "./runner_support.js";
import type { BBox, Block, Document, Page, Source, Table } from "../models/document.js";

const SERVE =
  "Set DOCLING_BASE_URL to the docling-serve base URL, for example http://localhost:5001.";

const LABELS: Record<string, Block["type"]> = {
  title: "title",
  section_header: "heading",
  paragraph: "paragraph",
  text: "paragraph",
  list_item: "list_item",
  caption: "caption",
  footnote: "footnote",
  page_header: "header",
  page_footer: "footer",
  code: "code",
  formula: "formula",
};

const COLLECTIONS = new Set(["texts", "tables", "groups", "pictures", "key_value_items"]);

interface Cell {
  text: string;
  column_header?: boolean;
}

interface MapState {
  pages: Map<number, { width?: number; height?: number }>;
  blocks: Block[];
  tables: Table[];
  seen: Set<string>;
  usedPages: Set<number>;
}

/** Parse with docling-serve and map a DoclingDocument export to a Document. */
export class DoclingAdapter implements Adapter {
  readonly id = "docling";
  readonly variants: readonly string[] = [];
  readonly variant: string | null;
  readonly options: Record<string, unknown>;

  /**
   * @example
   * ```ts
   * new DoclingAdapter(null, { baseUrl: "http://localhost:5001" });
   * ```
   */
  constructor(variant: string | null, options: Record<string, unknown>) {
    this.variant = variant;
    this.options = options;
  }

  /**
   * Return whether `DOCLING_BASE_URL` or `options.baseUrl` is set.
   *
   * @example
   * ```ts
   * const check = await new DoclingAdapter(null, {}).available();
   * ```
   */
  async available(): Promise<{ ok: boolean; reason?: string }> {
    if (serveBase(this.options) === null) return { ok: false, reason: SERVE };
    return { ok: true };
  }

  /**
   * Return the options that identify a cached Docling parse.
   *
   * @example
   * ```ts
   * new DoclingAdapter(null, { doOcr: true }).cacheIdentity();
   * ```
   */
  cacheIdentity(): Record<string, unknown> {
    const identity: Record<string, unknown> = { options: { ...this.options } };
    if (this.variant !== null) identity.variant = this.variant;
    return identity;
  }

  /**
   * POST `path` to `/v1/convert/file` and return the JSON export.
   *
   * @example
   * ```ts
   * const raw = await new DoclingAdapter(null, {}).fetch("files/sample.pdf");
   * ```
   */
  async fetch(filePath: string): Promise<RawResult> {
    let bytes: Buffer;
    try {
      bytes = await readFile(filePath);
    } catch {
      throw new AdapterError(`docling cannot read ${filePath}: path not found`);
    }
    const base = serveBase(this.options);
    if (base === null) throw new AdapterError(SERVE);
    const form = new FormData();
    form.append("files", new Blob([bytes]), path.basename(filePath));
    form.append("to_formats", "json");
    const started = Date.now();
    let response: Response;
    try {
      response = await fetch(`${base}/v1/convert/file`, { method: "POST", body: form });
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      throw new AdapterError(`docling-serve request for ${filePath} failed: ${detail}`);
    }
    if (!response.ok) {
      const detail = (await response.text()).slice(0, 200);
      throw new AdapterError(
        `docling-serve returned HTTP ${response.status} for ${filePath}: ${detail}`,
      );
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new AdapterError(`docling-serve response for ${filePath} was not JSON`);
    }
    const raw = parseServeResponse(body, filePath);
    if (raw.latencyMs === 0) raw.latencyMs = Date.now() - started;
    return raw;
  }

  /**
   * Map a DoclingDocument export. This method does not call the network.
   *
   * @example
   * ```ts
   * const document = adapter.toDocument(raw, source);
   * ```
   */
  toDocument(raw: RawResult, source: Source): Document {
    return doclingToDocument(raw.payload, source, raw.parserVersion);
  }
}

/**
 * Read a docling-serve `/v1/convert/file` JSON body.
 *
 * `parserVersion` is `body.version` when present, otherwise
 * `docling-document-` plus the export schema version.
 *
 * @example
 * ```ts
 * const raw = parseServeResponse(
 *   { status: "success", document: { json_content: { schema_name: "DoclingDocument" } } },
 *   "a.pdf",
 * );
 * ```
 */
export function parseServeResponse(body: unknown, filePath: string): RawResult {
  if (!isRecord(body)) {
    throw new AdapterError(`docling-serve response for ${filePath} was not a JSON object`);
  }
  if (body.status === "failure") {
    const errors = Array.isArray(body.errors) ? body.errors.map((item) => String(item)).join("; ") : "";
    throw new AdapterError(`docling failed on ${filePath}: ${errors || "conversion failed"}`);
  }
  const document = isRecord(body.document) ? body.document : null;
  const payload = document === null ? null : document.json_content;
  if (!isRecord(payload)) {
    throw new AdapterError(
      `docling-serve response for ${filePath} has no document.json_content. Request to_formats=json.`,
    );
  }
  const processing = body.processing_time;
  const latencyMs = typeof processing === "number" && Number.isFinite(processing) ? processing * 1000 : 0;
  let parserVersion = "unknown";
  if (typeof body.version === "string" && body.version) parserVersion = body.version;
  else if (typeof payload.version === "string" && payload.version) {
    parserVersion = `docling-document-${payload.version}`;
  }
  return { payload, parserVersion, latencyMs, costUsd: 0 };
}

/**
 * Map one DoclingDocument JSON object to a Document.
 *
 * Reading order is the `body` tree, then `furniture` items that were not
 * already visited. Table captions are emitted just before their table. Merged
 * cells are repeated across every cell they span. Bottom-left boxes are
 * flipped using the page height; 0–1 boxes are scaled by the page size.
 *
 * @example
 * ```ts
 * const document = doclingToDocument({ schema_name: "DoclingDocument" }, source, "2.87.0");
 * ```
 */
export function doclingToDocument(payload: unknown, source: Source, parserVersion: string): Document {
  if (!isRecord(payload) || payload.schema_name !== "DoclingDocument") {
    throw new AdapterError(
      'docling payload must be a DoclingDocument JSON object (schema_name "DoclingDocument")',
    );
  }
  if (!parserVersion) {
    throw new AdapterError("docling parserVersion is empty. Record the Docling version that ran.");
  }
  const pages = readPages(payload);
  const state: MapState = { pages, blocks: [], tables: [], seen: new Set(), usedPages: new Set() };
  walkChildren(payload, record(payload.body).children, state);
  walkChildren(payload, record(payload.furniture).children, state);
  for (const number of state.usedPages) {
    if (!pages.has(number)) pages.set(number, {});
  }
  const ordered: Page[] = [...pages.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([number, size]) => {
      const page: Page = { number };
      if (size.width !== undefined) page.width = size.width;
      if (size.height !== undefined) page.height = size.height;
      return page;
    });
  const stored: Source = { path: source.path, sha256: source.sha256, mime: source.mime };
  if (source.pageCount !== undefined) stored.pageCount = source.pageCount;
  return {
    specVersion: "1.0.0",
    source: stored,
    meta: { parser: "docling", parserVersion, adapterVersion: packageVersion() },
    pages: ordered,
    blocks: state.blocks,
    tables: state.tables,
  };
}

function serveBase(options: Record<string, unknown>): string | null {
  const chosen = options.baseUrl ?? options.url ?? process.env.DOCLING_BASE_URL;
  if (typeof chosen !== "string" || chosen === "") return null;
  return chosen.endsWith("/") ? chosen.slice(0, -1) : chosen;
}

function walkChildren(payload: Record<string, unknown>, children: unknown, state: MapState): void {
  if (!Array.isArray(children)) return;
  for (const child of children) walk(payload, refOf(child), state);
}

function walk(payload: Record<string, unknown>, ref: string | null, state: MapState): void {
  if (ref === null || state.seen.has(ref)) return;
  const pointer = parsePointer(ref);
  if (pointer === null) return;
  const item = collectionItem(payload, pointer.kind, pointer.index);
  if (item === null) return;
  state.seen.add(ref);
  if (pointer.kind === "groups") {
    walkChildren(payload, item.children, state);
    return;
  }
  if (pointer.kind === "tables") {
    walkChildren(payload, item.captions, state);
    state.tables.push(mapTable(item, state.tables.length + 1, state));
    walkChildren(payload, item.children, state);
    walkChildren(payload, item.footnotes, state);
    return;
  }
  if (pointer.kind === "pictures" || pointer.kind === "key_value_items") {
    walkChildren(payload, item.children, state);
    emitText(item, state);
    return;
  }
  emitText(item, state);
}

function emitText(item: Record<string, unknown>, state: MapState): void {
  if (typeof item.text !== "string" || item.text === "") return;
  const label = typeof item.label === "string" ? item.label : "";
  const type = LABELS[label] ?? "other";
  const placed = placement(item, state);
  const block: Block = { id: `b${state.blocks.length + 1}`, type, text: item.text, page: placed.page };
  if (type === "heading") {
    block.level = typeof item.level === "number" && Number.isInteger(item.level) && item.level >= 1 ? item.level : 1;
  }
  if (placed.bbox !== undefined) block.bbox = placed.bbox;
  state.blocks.push(block);
}

function mapTable(item: Record<string, unknown>, number: number, state: MapState): Table {
  const placed = placement(item, state);
  const split = tableRows(record(item.data));
  const table: Table = {
    id: `t${number}`,
    page: placed.page,
    ...(placed.bbox !== undefined ? { bbox: placed.bbox } : {}),
    ...(split.header !== null ? { header: split.header } : {}),
    rows: split.rows,
  };
  return table;
}

function tableRows(data: Record<string, unknown>): { header: string[] | null; rows: string[][] } {
  const grid = buildGrid(data);
  const width = grid.reduce((max, row) => Math.max(max, row.length), 0);
  let headerCount = 0;
  for (const row of grid) {
    if (row.length > 0 && row.every((cell) => cell.column_header === true)) headerCount += 1;
    else break;
  }
  let header: string[] | null = null;
  if (headerCount > 0) {
    header = [];
    for (let col = 0; col < width; col += 1) {
      const parts: string[] = [];
      for (const row of grid.slice(0, headerCount)) {
        const text = cellText(row, col);
        if (text && (parts.length === 0 || parts[parts.length - 1] !== text)) parts.push(text);
      }
      header.push(parts.join(" "));
    }
  }
  const rows = grid.slice(headerCount).map((row) => {
    const cells: string[] = [];
    for (let col = 0; col < width; col += 1) cells.push(cellText(row, col));
    return cells;
  });
  return { header, rows };
}

function buildGrid(data: Record<string, unknown>): Cell[][] {
  const raw = data.grid;
  if (Array.isArray(raw) && raw.length > 0 && raw.every((row) => Array.isArray(row))) {
    return raw.map((row) =>
      row.map((cell) => (isRecord(cell) ? { text: typeof cell.text === "string" ? cell.text : "", column_header: cell.column_header === true } : { text: "" })),
    );
  }
  if (!Array.isArray(data.table_cells)) return [];
  let rowCount = asIndex(data.num_rows) ?? 0;
  let colCount = asIndex(data.num_cols) ?? 0;
  for (const cell of data.table_cells) {
    if (!isRecord(cell)) continue;
    const endRow = asIndex(cell.end_row_offset_idx);
    const endCol = asIndex(cell.end_col_offset_idx);
    if (endRow !== null) rowCount = Math.max(rowCount, endRow);
    if (endCol !== null) colCount = Math.max(colCount, endCol);
  }
  const grid: Cell[][] = [];
  for (let row = 0; row < rowCount; row += 1) {
    const line: Cell[] = [];
    for (let col = 0; col < colCount; col += 1) line.push({ text: "", column_header: false });
    grid.push(line);
  }
  for (const cell of data.table_cells) {
    if (!isRecord(cell)) continue;
    const startRow = asIndex(cell.start_row_offset_idx);
    const endRow = asIndex(cell.end_row_offset_idx);
    const startCol = asIndex(cell.start_col_offset_idx);
    const endCol = asIndex(cell.end_col_offset_idx);
    if (startRow === null || endRow === null || startCol === null || endCol === null) continue;
    const text = typeof cell.text === "string" ? cell.text : "";
    const header = cell.column_header === true;
    for (let row = startRow; row < endRow; row += 1) {
      for (let col = startCol; col < endCol; col += 1) {
        const line = grid[row];
        if (line !== undefined && col < line.length) line[col] = { text, column_header: header };
      }
    }
  }
  return grid;
}

function cellText(row: Cell[], col: number): string {
  return row[col]?.text ?? "";
}

function placement(item: Record<string, unknown>, state: MapState): { page: number; bbox?: BBox } {
  const prov = Array.isArray(item.prov) ? item.prov : [];
  const first = isRecord(prov[0]) ? prov[0] : {};
  const pageNo = first.page_no;
  const page = typeof pageNo === "number" && Number.isInteger(pageNo) && pageNo >= 1 ? pageNo : 1;
  state.usedPages.add(page);
  const size = state.pages.get(page) ?? {};
  const box = isRecord(first.bbox) ? first.bbox : null;
  const bbox = convertBBox(box, size.width, size.height);
  return bbox === undefined ? { page } : { page, bbox };
}

function convertBBox(
  box: Record<string, unknown> | null,
  width: number | undefined,
  height: number | undefined,
): BBox | undefined {
  if (box === null) return undefined;
  let left = asNumber(box.l);
  let top = asNumber(box.t);
  let right = asNumber(box.r);
  let bottom = asNumber(box.b);
  if (left === null || top === null || right === null || bottom === null) return undefined;
  if (
    width !== undefined &&
    height !== undefined &&
    width > 1 &&
    height > 1 &&
    Math.max(Math.abs(left), Math.abs(top), Math.abs(right), Math.abs(bottom)) <= 1
  ) {
    left *= width;
    right *= width;
    top *= height;
    bottom *= height;
  }
  if (box.coord_origin === "BOTTOMLEFT") {
    if (height === undefined || height <= 0) return undefined;
    top = height - top;
    bottom = height - bottom;
  }
  const x0 = left <= right ? left : right;
  const x1 = left <= right ? right : left;
  const y0 = top <= bottom ? top : bottom;
  const y1 = top <= bottom ? bottom : top;
  return [round4(x0), round4(y0), round4(x1), round4(y1)];
}

function readPages(payload: Record<string, unknown>): Map<number, { width?: number; height?: number }> {
  const found = new Map<number, { width?: number; height?: number }>();
  const raw = payload.pages;
  const items: Array<[unknown, unknown]> = [];
  if (isRecord(raw)) {
    for (const [key, value] of Object.entries(raw)) items.push([key, value]);
  } else if (Array.isArray(raw)) {
    for (const value of raw) items.push([null, value]);
  }
  for (const [key, value] of items) {
    if (!isRecord(value)) continue;
    let number = typeof value.page_no === "number" && Number.isInteger(value.page_no) ? value.page_no : null;
    if (number === null || number < 1) number = digits(key);
    if (number === null) continue;
    const size = record(value.size);
    const page: { width?: number; height?: number } = {};
    const pageWidth = positive(size.width);
    const pageHeight = positive(size.height);
    if (pageWidth !== null) page.width = pageWidth;
    if (pageHeight !== null) page.height = pageHeight;
    found.set(number, page);
  }
  return found;
}

function collectionItem(payload: Record<string, unknown>, kind: string, index: number): Record<string, unknown> | null {
  if (!COLLECTIONS.has(kind)) return null;
  const collection = payload[kind];
  if (!Array.isArray(collection) || index < 0 || index >= collection.length) return null;
  const item = collection[index];
  return isRecord(item) ? item : null;
}

function parsePointer(ref: string): { kind: string; index: number } | null {
  if (!ref.startsWith("#/")) return null;
  const parts = ref.slice(2).split("/");
  if (parts.length !== 2) return null;
  const kind = parts[0] ?? "";
  const index = parts[1] ?? "";
  if (!index || [...index].some((char) => !"0123456789".includes(char))) return null;
  return { kind, index: Number(index) };
}

function refOf(node: unknown): string | null {
  if (typeof node === "string") return node;
  if (isRecord(node) && typeof node.$ref === "string") return node.$ref;
  return null;
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function asNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function positive(value: unknown): number | null {
  const number = asNumber(value);
  if (number === null || number <= 0) return null;
  return number;
}

function asIndex(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) return null;
  return value;
}

function digits(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value) && value >= 1) return value;
  if (typeof value === "string" && value && [...value].every((char) => "0123456789".includes(char))) {
    const number = Number(value);
    return number >= 1 ? number : null;
  }
  return null;
}

function round4(value: number): number {
  return Math.floor(value * 10000 + 0.5) / 10000;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
