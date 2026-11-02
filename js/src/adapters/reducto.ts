/**
 * Reducto adapter.
 *
 * Both languages call the Reducto REST API (upload, parse, optional extract).
 * Mapping is pure. `parserVersion` is the model id, default `r-1`.
 *
 * @example
 * ```ts
 * import { ReductoAdapter } from "./adapters/reducto.js";
 *
 * const adapter = new ReductoAdapter(null, { model: "r-1" });
 * const check = await adapter.available();
 * ```
 */

import { readFile, stat } from "node:fs/promises";
import path from "node:path";

import { AdapterError, type Adapter, type RawResult } from "./base.js";
import { packageVersion } from "./runner_support.js";
import type {
  BBox,
  Block,
  Chunk,
  Citation,
  Document,
  Field,
  Page,
  Source,
  Table,
} from "../models/document.js";

/**
 * List prices effective 2026-09-01.
 * Source: https://docs.reducto.ai/reference/credit-usage
 * Read 2026-11-02. `usage.credits` is not dollars.
 * Legacy complex pages are $30/1,000, but the response does not mark them,
 * so legacy parse uses the standard-page rate of $15/1,000.
 */
export const PARSE_USD_PER_THOUSAND: Record<string, number> = { "r-1": 10, legacy: 15 };
export const EXTRACT_USD_PER_THOUSAND = 20;
export const DEFAULT_MODEL = "r-1";
export const API_KEY =
  "REDUCTO_API_KEY is not set. Create a key at https://studio.reducto.ai and export it.";

const DEFAULT_BASE = "https://platform.reducto.ai";
const RESERVED = new Set([
  "apiKey",
  "api_key",
  "baseUrl",
  "base_url",
  "extractSchema",
  "extract_schema",
  "model",
  "pageSize",
  "page_size",
  "pages",
  "timeoutS",
  "timeout_s",
]);
const SPACE = new Set([" ", "\t", "\r", "\n", "\u00a0"]);
const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};
const DONE = new Set(["COMPLETED"]);
const FAIL = new Set(["FAILED", "CANCELLED", "CANCELED"]);

/** Clock used while polling. Tests replace these to avoid waiting. */
export const pollControl = {
  now: (): number => Date.now() / 1000,
  sleep: (seconds: number): Promise<void> =>
    new Promise((resolve) => {
      setTimeout(resolve, seconds * 1000);
    }),
};

type BlockType = Block["type"];
type Size = [number | undefined, number | undefined];

interface Hit {
  id: string;
  page: number;
  text: string;
  raw?: [number, number, number, number];
}

interface MapState {
  blocks: Block[];
  tables: Table[];
  chunks: Chunk[];
  emitted: Hit[];
  usedPages: Set<number>;
}

/** Upload a file to Reducto and map parse (and optional extract) to a Document. */
export class ReductoAdapter implements Adapter {
  readonly id = "reducto";
  readonly variants: readonly string[] = [];
  readonly variant: string | null;
  readonly options: Record<string, unknown>;

  /**
   * @example
   * ```ts
   * new ReductoAdapter(null, {});
   * ```
   */
  constructor(variant: string | null, options: Record<string, unknown>) {
    this.variant = variant;
    this.options = options;
  }

  /**
   * Return whether `REDUCTO_API_KEY` is set.
   *
   * @example
   * ```ts
   * const check = await new ReductoAdapter(null, {}).available();
   * ```
   */
  available(): Promise<{ ok: boolean; reason?: string }> {
    if (!apiKey()) return Promise.resolve({ ok: false, reason: API_KEY });
    return Promise.resolve({ ok: true });
  }

  /**
   * Return the model, schema path, page sizes, and options that change output.
   *
   * The API key, base URL, and timeout are omitted. The schema path is the
   * path string, not a hash of the file. Edit the schema and pass `--refresh`.
   *
   * @example
   * ```ts
   * new ReductoAdapter(null, { model: "legacy" }).cacheIdentity();
   * ```
   */
  cacheIdentity(): Record<string, unknown> {
    return {
      model: modelOf(this.options),
      extract_schema: schemaOption(this.options) ?? null,
      page_size: picked(this.options, "page_size", "pageSize"),
      pages: "pages" in this.options ? (this.options.pages ?? null) : null,
      options: publicOptions(this.options),
    };
  }

  /**
   * Upload `filePath`, parse it, and optionally extract fields.
   *
   * @example
   * ```ts
   * const raw = await new ReductoAdapter(null, {}).fetch("files/sample.pdf");
   * ```
   */
  async fetch(filePath: string): Promise<RawResult> {
    if (this.variant !== null) {
      throw new AdapterError('reducto has no variants. Use the id "reducto".');
    }
    await assertFile(filePath);
    const key = apiKey();
    if (!key) throw new AdapterError(API_KEY);
    const timeoutS = timeoutSeconds(this.options);
    const model = modelOf(this.options);
    const base = baseUrl(this.options);
    const schema = await loadSchema(this.options, filePath);
    const started = pollControl.now();
    const fileId = await upload(filePath, key, base);
    const parsed = await parseJob(filePath, key, base, fileId, this.options, model, timeoutS);
    let extracted: Record<string, unknown> | undefined;
    if (schema) {
      const jobId = jobIdOf(parsed);
      if (!jobId) {
        throw new AdapterError(
          `reducto parse for ${filePath} did not return a job_id, so extract cannot run`,
        );
      }
      extracted = await extractJob(filePath, key, base, jobId, schema, timeoutS);
    }
    const elapsed = (pollControl.now() - started) * 1000;
    const payload: Record<string, unknown> = { parse: parsed, pages: pageSpecs(this.options) };
    if (extracted) payload.extract = extracted;
    return {
      payload,
      parserVersion: reportedModel(parsed, model),
      latencyMs: elapsed,
      costUsd: reductoCostUsd(model, billedPages(parsed), extracted !== undefined),
    };
  }

  /**
   * Map a recorded Reducto result. This method does not call the API.
   *
   * @example
   * ```ts
   * const document = adapter.toDocument(raw, source);
   * ```
   */
  toDocument(raw: RawResult, source: Source): Document {
    return reductoToDocument(raw.payload, source, raw.parserVersion);
  }
}

/**
 * Return USD for `pageCount` pages.
 *
 * `r-1` parse is $10 per 1,000 pages. Legacy parse is $15 per 1,000 pages.
 * Extract adds $20 per 1,000 pages. An unknown model has no parse price.
 * `usage.credits` is not a dollar amount.
 *
 * @example
 * ```ts
 * reductoCostUsd("r-1", 1, false);
 * ```
 */
export function reductoCostUsd(model: string, pageCount: number, extracted: boolean): number {
  if (typeof pageCount !== "number" || !Number.isInteger(pageCount) || pageCount < 1) return 0;
  let perThousand = PARSE_USD_PER_THOUSAND[model] ?? 0;
  if (extracted) perThousand += EXTRACT_USD_PER_THOUSAND;
  return (pageCount * perThousand) / 1000;
}

/**
 * Map one Reducto parse response, plus an optional extract response, to a Document.
 *
 * Boxes are normalized 0–1 `{left, top, width, height}`. They are scaled by
 * the page size in the payload's `pages` list and omitted when that size is missing.
 *
 * @example
 * ```ts
 * const document = reductoToDocument({ result: { chunks: [] } }, source, "r-1");
 * ```
 */
export function reductoToDocument(payload: unknown, source: Source, parserVersion: string): Document {
  if (!isRecord(payload)) {
    throw new AdapterError("reducto payload must be a JSON object from POST /parse");
  }
  if (!parserVersion) {
    throw new AdapterError("reducto parserVersion is empty. Record the model id, for example r-1.");
  }
  const split = splitPayload(payload);
  const result = split.parsed.result;
  if (isRecord(result) && result.type === "url") {
    throw new AdapterError("reducto result is a URL. Fetch result.url before mapping.");
  }
  const chunksRaw = chunksOf(result);
  if (!chunksRaw) {
    throw new AdapterError("reducto payload needs result.chunks. Fetch the parse result before mapping.");
  }
  const sizes = pageTable(split.pages);
  const state: MapState = { blocks: [], tables: [], chunks: [], emitted: [], usedPages: new Set() };
  let headings: string[] = [];
  for (const chunk of chunksRaw) {
    if (!isRecord(chunk)) continue;
    headings = oneChunk(chunk, state, headings, sizes.explicit, sizes.defaultSize);
  }
  for (const number of state.usedPages) {
    if (!sizes.explicit.has(number)) sizes.explicit.set(number, sizes.defaultSize);
  }
  const pages = [...sizes.explicit.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([number, size]) => pageOf(number, size));
  const fields = split.extracted === undefined ? undefined : fieldsOf(extractResult(split.extracted), sizes, state.emitted);
  const document: Document = {
    specVersion: "1.0.0",
    source,
    meta: { parser: "reducto", parserVersion, adapterVersion: packageVersion() },
    pages,
    blocks: state.blocks,
    tables: state.tables,
    chunks: state.chunks,
  };
  if (fields) document.fields = fields;
  return document;
}

function pageOf(number: number, size: Size): Page {
  const page: Page = { number };
  if (size[0] !== undefined) page.width = size[0];
  if (size[1] !== undefined) page.height = size[1];
  return page;
}

function oneChunk(
  chunk: Record<string, unknown>,
  state: MapState,
  headings: string[],
  explicit: Map<number, Size>,
  defaultSize: Size,
): string[] {
  const text = typeof chunk.content === "string" ? chunk.content : "";
  const ids: string[] = [];
  const pages = new Set<number>();
  let stack = [...headings];
  if (Array.isArray(chunk.blocks)) {
    for (const block of chunk.blocks) {
      if (!isRecord(block)) continue;
      const emitted = emit(block, state, explicit, defaultSize);
      if (!emitted) continue;
      ids.push(emitted.id);
      pages.add(emitted.page);
      if (emitted.kind === "title" || emitted.kind === "section header") {
        stack = applyHeading(stack, emitted.kind, emitted.text);
      }
    }
  }
  const mapped: Chunk = {
    id: `c${state.chunks.length + 1}`,
    text,
    blockIds: ids,
    pages: [...pages].sort((left, right) => left - right),
  };
  if (stack.length > 0) mapped.headingPath = [...stack];
  state.chunks.push(mapped);
  return stack;
}

function emit(
  block: Record<string, unknown>,
  state: MapState,
  explicit: Map<number, Size>,
  defaultSize: Size,
): { id: string; page: number; kind: string; text: string } | undefined {
  const text = typeof block.content === "string" ? block.content : "";
  if (!trim(text)) return undefined;
  const page = pageFromBox(block.bbox);
  const size = sizeFor(page, explicit, defaultSize);
  const box = bboxOf(block.bbox, size[0], size[1]);
  const raw = rawBox(block.bbox);
  const kind = kindOf(block.type);
  if (kind === "table") {
    const id = `t${state.tables.length + 1}`;
    const matrix = tableMatrix(text);
    const table: Table = {
      id,
      page,
      ...(box ? { bbox: box } : {}),
      ...(matrix.header ? { header: matrix.header } : {}),
      rows: matrix.rows,
      markdown: text,
    };
    state.tables.push(table);
    state.usedPages.add(page);
    state.emitted.push({ id, page, text, raw });
    return { id, page, kind, text };
  }
  const classified = classify(kind, text);
  const id = `b${state.blocks.length + 1}`;
  const mapped: Block = { id, type: classified.type, text, page };
  if (classified.level !== undefined) mapped.level = classified.level;
  if (box) mapped.bbox = box;
  state.blocks.push(mapped);
  state.usedPages.add(page);
  state.emitted.push({ id, page, text, raw });
  return { id, page, kind, text };
}

function applyHeading(stack: string[], kind: string, text: string): string[] {
  const level = kind === "title" ? 1 : hashLevel(text);
  return [...stack.slice(0, level - 1), text];
}

function classify(kind: string, text: string): { type: BlockType; level?: number } {
  if (kind === "title") return { type: "title" };
  if (kind === "section header") return { type: "heading", level: hashLevel(text) };
  if (kind === "header") return { type: "header" };
  if (kind === "footer") return { type: "footer" };
  if (kind === "text") return { type: "paragraph" };
  if (kind === "list item") return { type: "list_item" };
  if (kind === "figure") return { type: "caption" };
  return { type: "other" };
}

function kindOf(value: unknown): string {
  if (typeof value !== "string") return "";
  return value
    .trim()
    .toLowerCase()
    .split(" ")
    .filter((part) => part.length > 0)
    .join(" ");
}

function tableMatrix(content: string): { header?: string[]; rows: string[][] } {
  let grid: string[][] | undefined;
  if (content.toLowerCase().includes("<tr")) grid = htmlGrid(content);
  if (!grid) grid = markdownRows(content);
  if (!grid || grid.length === 0) return { rows: [[content]] };
  const width = Math.max(...grid.map((row) => row.length));
  const padded = grid.map((row) => row.concat(Array.from({ length: width - row.length }, () => "")));
  return { header: padded[0], rows: padded.slice(1) };
}

function markdownRows(content: string): string[][] | undefined {
  const rows: string[][] = [];
  let sawPipe = false;
  for (const line of content.split("\n")) {
    const cells = pipeCells(line);
    if (!cells) continue;
    sawPipe = true;
    if (separator(cells)) continue;
    rows.push(cells);
  }
  if (!sawPipe || rows.length === 0) return undefined;
  return rows;
}

function pipeCells(line: string): string[] | undefined {
  const trimmed = trim(line.split("\r").join(""));
  if (!trimmed.includes("|")) return undefined;
  let parts = trimmed.split("|");
  if (trimmed.startsWith("|")) parts = parts.slice(1);
  if (trimmed.endsWith("|")) parts = parts.slice(0, -1);
  return parts.map((part) => trim(part));
}

function separator(cells: string[]): boolean {
  if (cells.length === 0) return false;
  for (const cell of cells) {
    if (!cell.includes("-")) return false;
    for (const char of cell) {
      if (char !== "-" && char !== ":" && char !== " ") return false;
    }
  }
  return true;
}

function htmlGrid(html: string): string[][] | undefined {
  const lower = html.toLowerCase();
  if (!lower.includes("<tr")) return undefined;
  const rows: Array<Array<[string, number, number]>> = [];
  let index = 0;
  while (index < html.length) {
    const start = lower.indexOf("<tr", index);
    if (start < 0) break;
    const nameEnd = start + 3;
    const boundary = lower[nameEnd] ?? "";
    if (nameEnd < html.length && !" >/\t\r\n".includes(boundary)) {
      index = nameEnd;
      continue;
    }
    const close = lower.indexOf("</tr>", nameEnd);
    if (close < 0) break;
    const cells = htmlCells(html.slice(nameEnd, close), lower.slice(nameEnd, close));
    if (cells.length > 0) rows.push(cells);
    index = close + 5;
  }
  if (rows.length === 0) return undefined;
  return place(rows);
}

function htmlCells(row: string, lower: string): Array<[string, number, number]> {
  const cells: Array<[string, number, number]> = [];
  let index = 0;
  while (index < row.length) {
    const td = lower.indexOf("<td", index);
    const th = lower.indexOf("<th", index);
    if (td < 0 && th < 0) break;
    const start = td < 0 || (th >= 0 && th < td) ? th : td;
    const nameEnd = start + 3;
    const boundary = lower[nameEnd] ?? "";
    if (nameEnd < row.length && !" >/\t\r\n".includes(boundary)) {
      index = nameEnd;
      continue;
    }
    const tag = lower.slice(start + 1, start + 3);
    const gt = lower.indexOf(">", nameEnd);
    if (gt < 0) break;
    const colspan = attrInt(lower.slice(nameEnd, gt), "colspan");
    const rowspan = attrInt(lower.slice(nameEnd, gt), "rowspan");
    const spanC = colspan === undefined || colspan < 1 ? 1 : Math.min(colspan, 32);
    const spanR = rowspan === undefined || rowspan < 1 ? 1 : Math.min(rowspan, 32);
    if (lower[gt - 1] === "/") {
      cells.push(["", spanC, spanR]);
      index = gt + 1;
      continue;
    }
    const close = lower.indexOf(`</${tag}>`, gt + 1);
    if (close < 0) break;
    cells.push([htmlText(row.slice(gt + 1, close)), spanC, spanR]);
    index = close + tag.length + 3;
  }
  return cells;
}

function attrInt(attrs: string, name: string): number | undefined {
  let start = 0;
  while (start < attrs.length) {
    const at = attrs.indexOf(name, start);
    if (at < 0) return undefined;
    if (at > 0 && !" \t\r\n/".includes(attrs[at - 1] ?? "")) {
      start = at + name.length;
      continue;
    }
    let cursor = at + name.length;
    while (cursor < attrs.length && " \t\r\n".includes(attrs[cursor] ?? "")) cursor += 1;
    if (cursor >= attrs.length || attrs[cursor] !== "=") {
      start = at + name.length;
      continue;
    }
    cursor += 1;
    while (cursor < attrs.length && " \t\r\n".includes(attrs[cursor] ?? "")) cursor += 1;
    let token = "";
    if (cursor < attrs.length && (attrs[cursor] === '"' || attrs[cursor] === "'")) {
      const quote = attrs[cursor] ?? "";
      cursor += 1;
      const end = attrs.indexOf(quote, cursor);
      token = end < 0 ? attrs.slice(cursor) : attrs.slice(cursor, end);
    } else {
      let end = cursor;
      while (end < attrs.length && !" \t\r\n>/".includes(attrs[end] ?? "")) end += 1;
      token = attrs.slice(cursor, end);
    }
    if (token && [...token].every((char) => "0123456789".includes(char))) return Number(token);
    start = at + name.length;
  }
  return undefined;
}

function htmlText(fragment: string): string {
  const parts: string[] = [];
  let index = 0;
  while (index < fragment.length) {
    const char = fragment[index] ?? "";
    if (char === "<") {
      const gt = fragment.indexOf(">", index + 1);
      if (gt < 0) break;
      index = gt + 1;
      continue;
    }
    if (char === "&") {
      const semi = fragment.indexOf(";", index + 1);
      if (semi < 0 || semi - index > 12) {
        parts.push("&");
        index += 1;
        continue;
      }
      parts.push(entity(fragment.slice(index + 1, semi)));
      index = semi + 1;
      continue;
    }
    parts.push(char);
    index += 1;
  }
  return parts.join("");
}

function entity(token: string): string {
  const named = ENTITIES[token];
  if (named !== undefined) return named;
  if (token.startsWith("#") && token.length > 1 && [...token.slice(1)].every((char) => "0123456789".includes(char))) {
    const code = Number(token.slice(1));
    if (code > 0 && code < 0x110000) return String.fromCodePoint(code);
  }
  return `&${token};`;
}

function place(rows: Array<Array<[string, number, number]>>): string[][] | undefined {
  const occupied = new Map<string, string>();
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    let column = 0;
    for (const [text, colspan, rowspan] of rows[rowIndex] ?? []) {
      while (occupied.has(`${rowIndex},${column}`) && column <= 64) column += 1;
      for (let dr = 0; dr < rowspan; dr += 1) {
        for (let dc = 0; dc < colspan; dc += 1) occupied.set(`${rowIndex + dr},${column + dc}`, text);
      }
      column += colspan;
    }
  }
  if (occupied.size === 0) return undefined;
  let height = 0;
  let width = 0;
  for (const key of occupied.keys()) {
    const [row, col] = key.split(",").map((part) => Number(part));
    if ((row ?? 0) + 1 > height) height = (row ?? 0) + 1;
    if ((col ?? 0) + 1 > width) width = (col ?? 0) + 1;
  }
  if (height > 64 || width > 64) return undefined;
  const grid: string[][] = [];
  for (let row = 0; row < height; row += 1) {
    const line: string[] = [];
    for (let col = 0; col < width; col += 1) line.push(occupied.get(`${row},${col}`) ?? "");
    grid.push(line);
  }
  return grid;
}

function fieldsOf(
  result: unknown,
  sizes: { explicit: Map<number, Size>; defaultSize: Size },
  emitted: Hit[],
): Record<string, Field> | undefined {
  const found = flatten(Array.isArray(result) && result.length === 1 ? result[0] : result, "", sizes, emitted, Array.isArray(result) && result.length !== 1);
  return Object.keys(found).length > 0 ? found : undefined;
}

function flatten(
  node: unknown,
  prefix: string,
  sizes: { explicit: Map<number, Size>; defaultSize: Size },
  emitted: Hit[],
  indexed: boolean,
): Record<string, Field> {
  if (Array.isArray(node) && indexed) {
    const found: Record<string, Field> = {};
    node.forEach((item, index) => Object.assign(found, flatten(item, String(index), sizes, emitted, false)));
    return found;
  }
  if (isWrapper(node)) return placeValue(prefix, node.value, node.citations, sizes, emitted);
  if (isRecord(node)) {
    const found: Record<string, Field> = {};
    for (const [key, item] of Object.entries(node)) {
      if (!key) continue;
      const child = prefix ? `${prefix}.${key}` : key;
      Object.assign(found, flatten(item, child, sizes, emitted, false));
    }
    return found;
  }
  if (Array.isArray(node)) {
    if (node.length > 0 && node.every((item) => isRecord(item) && !isWrapper(item))) {
      const found: Record<string, Field> = {};
      node.forEach((item, index) => {
        const child = prefix ? `${prefix}.${index}` : String(index);
        Object.assign(found, flatten(item, child, sizes, emitted, false));
      });
      return found;
    }
    const values: unknown[] = [];
    const gathered: unknown[] = [];
    for (const item of node) {
      if (isWrapper(item)) {
        values.push(item.value);
        if (Array.isArray(item.citations)) gathered.push(...item.citations);
      } else values.push(item);
    }
    return placeValue(prefix, values, gathered, sizes, emitted);
  }
  return placeValue(prefix, node, undefined, sizes, emitted);
}

function placeValue(
  prefix: string,
  value: unknown,
  citations: unknown,
  sizes: { explicit: Map<number, Size>; defaultSize: Size },
  emitted: Hit[],
): Record<string, Field> {
  if (!prefix) return {};
  const mapped = citationsOf(citations, sizes, emitted);
  const field: Field = { value: scalar(value) };
  const confidence = confidenceOf(citations);
  if (confidence !== undefined) field.confidence = confidence;
  if (mapped.length > 0) field.citations = mapped;
  return { [prefix]: field };
}

function citationsOf(
  value: unknown,
  sizes: { explicit: Map<number, Size>; defaultSize: Size },
  emitted: Hit[],
): Citation[] {
  if (!Array.isArray(value)) return [];
  const found: Citation[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const page = pageFromBox(item.bbox);
    const size = sizeFor(page, sizes.explicit, sizes.defaultSize);
    const citation: Citation = { page };
    const blockId = blockIdOf(item, emitted);
    if (blockId) citation.blockId = blockId;
    const box = bboxOf(item.bbox, size[0], size[1]);
    if (box) citation.bbox = box;
    if (typeof item.content === "string" && item.content) citation.text = item.content;
    found.push(citation);
  }
  return found;
}

function blockIdOf(citation: Record<string, unknown>, emitted: Hit[]): string | undefined {
  const parent = isRecord(citation.parentBlock) ? citation.parentBlock : undefined;
  const parentBox = parent ? rawBox(parent.bbox) : undefined;
  const parentPage = parent && isRecord(parent.bbox) ? pageFromBox(parent.bbox) : undefined;
  const parentText = parent && typeof parent.content === "string" && parent.content ? parent.content : undefined;
  if (parentBox && parentPage !== undefined) {
    for (const item of emitted) {
      if (item.page === parentPage && item.raw && sameBox(item.raw, parentBox)) return item.id;
    }
  }
  const texts = [parentText, typeof citation.content === "string" && citation.content ? citation.content : undefined];
  for (const text of texts) {
    if (!text) continue;
    for (const item of emitted) if (item.text === text) return item.id;
  }
  return undefined;
}

function sameBox(left: [number, number, number, number], right: [number, number, number, number]): boolean {
  return left.every((value, index) => value === right[index]);
}

function confidenceOf(value: unknown): number | undefined {
  if (!Array.isArray(value)) return undefined;
  let parseScore: number | undefined;
  for (const item of value) {
    if (!isRecord(item) || !isRecord(item.granular_confidence)) continue;
    const extractScore = unit(item.granular_confidence.extract_confidence);
    if (extractScore !== undefined) return extractScore;
    if (parseScore === undefined) parseScore = unit(item.granular_confidence.parse_confidence);
  }
  return parseScore;
}

function isWrapper(value: unknown): value is { value: unknown; citations: unknown } {
  return isRecord(value) && "value" in value && "citations" in value;
}

function scalar(value: unknown): string | number | boolean | null {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  return canonicalJson(value);
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(jsonable(value));
}

function jsonable(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => jsonable(item));
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) out[key] = jsonable(value[key]);
    return out;
  }
  return String(value);
}

async function upload(filePath: string, key: string, base: string): Promise<string> {
  const bytes = await readFile(filePath);
  const form = new FormData();
  form.append("file", new Blob([bytes]), path.basename(filePath));
  const body = await exchange(filePath, "POST", `${base}/upload`, { Authorization: `Bearer ${key}` }, form);
  const fileId = body.file_id;
  if (typeof fileId !== "string" || !fileId.trim()) {
    throw new AdapterError(`reducto upload for ${filePath} did not return a file_id`);
  }
  return fileId.trim();
}

async function parseJob(
  filePath: string,
  key: string,
  base: string,
  fileId: string,
  options: Record<string, unknown>,
  model: string,
  timeoutS: number,
): Promise<Record<string, unknown>> {
  const body = await exchange(
    filePath,
    "POST",
    `${base}/parse`,
    { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    JSON.stringify(parseBody(fileId, options, model)),
  );
  return settle(filePath, key, base, body, timeoutS, "parse");
}

async function extractJob(
  filePath: string,
  key: string,
  base: string,
  jobId: string,
  schema: Record<string, unknown>,
  timeoutS: number,
): Promise<Record<string, unknown>> {
  const request = {
    input: `jobid://${jobId}`,
    instructions: { schema },
    settings: { citations: { enabled: true } },
  };
  const body = await exchange(
    filePath,
    "POST",
    `${base}/extract`,
    { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    JSON.stringify(request),
  );
  return settle(filePath, key, base, body, timeoutS, "extract");
}

async function settle(
  filePath: string,
  key: string,
  base: string,
  body: Record<string, unknown>,
  timeoutS: number,
  what: string,
): Promise<Record<string, unknown>> {
  const label = statusOf(body);
  if (FAIL.has(label)) {
    throw new AdapterError(
      `reducto job ${jobIdOf(body) || "unknown"} ${label} on ${filePath}: ${errorMessage(body)}`,
    );
  }
  if (DONE.has(label) && !resultReady(body)) {
    throw new AdapterError(`reducto ${what} for ${filePath} did not return a result`);
  }
  let current = body;
  if (!resultReady(current)) {
    const jobId = jobIdOf(current);
    if (!jobId) throw new AdapterError(`reducto ${what} for ${filePath} did not return a result`);
    current = await poll(filePath, key, base, jobId, timeoutS);
  }
  return inlineUrl(filePath, key, current);
}

async function poll(
  filePath: string,
  key: string,
  base: string,
  jobId: string,
  timeoutS: number,
): Promise<Record<string, unknown>> {
  const url = `${base}/job/${encodeURIComponent(jobId)}`;
  const headers = { Authorization: `Bearer ${key}` };
  let delay = 1;
  const started = pollControl.now();
  for (;;) {
    const body = await exchange(filePath, "GET", url, headers, undefined);
    const label = statusOf(body);
    if (DONE.has(label) && !resultReady(body)) {
      throw new AdapterError(`reducto job ${jobId} ${label} on ${filePath} did not return a result`);
    }
    if (DONE.has(label) || resultReady(body)) return body;
    if (FAIL.has(label)) {
      throw new AdapterError(`reducto job ${jobId} ${label} on ${filePath}: ${errorMessage(body)}`);
    }
    const remaining = timeoutS - (pollControl.now() - started);
    if (remaining <= 0) {
      throw new AdapterError(
        `reducto timed out after ${timeoutS}s waiting for job ${jobId} on ${filePath}. Raise options.timeout_s or retry the file.`,
      );
    }
    await pollControl.sleep(delay < remaining ? delay : remaining);
    delay = delay * 2 > 8 ? 8 : delay * 2;
  }
}

async function inlineUrl(
  filePath: string,
  key: string,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const result = body.result;
  if (!isRecord(result) || result.type !== "url") return body;
  const url = result.url;
  if (typeof url !== "string" || !url) {
    throw new AdapterError(`reducto result for ${filePath} is a URL with no url field`);
  }
  const fetched = await exchange(filePath, "GET", url, { Authorization: `Bearer ${key}` }, undefined, true);
  const chunks = Array.isArray(fetched) ? fetched : urlChunks(fetched);
  if (!Array.isArray(chunks)) {
    throw new AdapterError(`reducto result URL for ${filePath} did not contain chunks`);
  }
  body.result = { type: "full", chunks };
  return body;
}

function urlChunks(body: unknown): unknown[] | undefined {
  if (!isRecord(body)) return undefined;
  if (Array.isArray(body.chunks)) return body.chunks;
  if (isRecord(body.result) && Array.isArray(body.result.chunks)) return body.result.chunks;
  if (Array.isArray(body.result)) return body.result;
  return undefined;
}

async function exchange(
  filePath: string,
  method: string,
  url: string,
  headers: Record<string, string>,
  content: FormData | string | undefined,
  allowList = false,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetch(url, { method, headers, body: content });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new AdapterError(`reducto failed on ${filePath}: ${message}`);
  }
  const text = await response.text();
  const loaded = loads(response.status, text, filePath, phase(method, url));
  if (Array.isArray(loaded) && allowList) return { result: loaded };
  if (!isRecord(loaded)) throw new AdapterError(`reducto returned a non-object body for ${filePath}`);
  return loaded;
}

function phase(method: string, url: string): string {
  if (method === "POST" && url.endsWith("/upload")) return "uploading";
  if (method === "POST" && url.endsWith("/parse")) return "parsing";
  if (method === "POST" && url.endsWith("/extract")) return "extracting";
  if (url.includes("/job/")) return "polling";
  return "fetching the result";
}

function loads(status: number, text: string, filePath: string, what: string): unknown {
  if (status < 200 || status >= 300) {
    throw new AdapterError(
      `reducto returned HTTP ${status} while ${what} ${filePath}: ${snippet(text)}. Check the model and REDUCTO_API_KEY.`,
    );
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new AdapterError(
      `reducto returned a non-JSON body for ${filePath}. Check REDUCTO_BASE_URL and the API status.`,
    );
  }
}

function parseBody(fileId: string, options: Record<string, unknown>, model: string): Record<string, unknown> {
  const publicCopy = publicOptions(options);
  const settings = publicCopy.settings;
  delete publicCopy.settings;
  const merged: Record<string, unknown> = isRecord(settings) ? { ...settings } : {};
  merged.model = model;
  const body: Record<string, unknown> = { input: fileId };
  for (const key of Object.keys(publicCopy).sort()) body[key] = publicCopy[key];
  body.settings = merged;
  return body;
}

function pageSpecs(options: Record<string, unknown>): unknown[] {
  const found: unknown[] = [];
  if (Array.isArray(options.pages)) found.push(...options.pages);
  const size = picked(options, "page_size", "pageSize");
  if (Array.isArray(size) && size.length >= 2) found.push({ width: size[0], height: size[1] });
  return found;
}

function splitPayload(payload: Record<string, unknown>): {
  parsed: Record<string, unknown>;
  extracted: unknown;
  pages: unknown;
} {
  if (isRecord(payload.parse)) {
    return {
      parsed: payload.parse,
      extracted: "extract" in payload ? payload.extract : undefined,
      pages: payload.pages,
    };
  }
  return { parsed: payload, extracted: undefined, pages: payload.pages };
}

function extractResult(extracted: unknown): unknown {
  if (isRecord(extracted) && "result" in extracted) return extracted.result;
  return extracted;
}

function chunksOf(result: unknown): unknown[] | undefined {
  if (isRecord(result) && Array.isArray(result.chunks)) return result.chunks;
  if (Array.isArray(result)) return result;
  return undefined;
}

function pageTable(spec: unknown): { explicit: Map<number, Size>; defaultSize: Size } {
  const explicit = new Map<number, Size>();
  let defaultSize: Size = [undefined, undefined];
  if (!Array.isArray(spec)) return { explicit, defaultSize };
  for (const item of spec) {
    if (!isRecord(item)) continue;
    const width = positive(item.width);
    const height = positive(item.height);
    const number = pageNumber(item.number ?? item.page);
    if (number !== undefined) explicit.set(number, [width, height]);
    else if (width !== undefined || height !== undefined) defaultSize = [width, height];
  }
  return { explicit, defaultSize };
}

function sizeFor(number: number, explicit: Map<number, Size>, defaultSize: Size): Size {
  const found = explicit.get(number) ?? defaultSize;
  return [found[0] ?? defaultSize[0], found[1] ?? defaultSize[1]];
}

function billedPages(body: Record<string, unknown>): number {
  if (isRecord(body.usage)) {
    const count = body.usage.num_pages;
    if (typeof count === "number" && Number.isInteger(count) && count >= 0) return count;
  }
  return distinctPages(body);
}

function distinctPages(body: Record<string, unknown>): number {
  const found = new Set<number>();
  for (const chunk of chunksOf(body.result) ?? []) {
    if (!isRecord(chunk) || !Array.isArray(chunk.blocks)) continue;
    for (const block of chunk.blocks) if (isRecord(block)) found.add(pageFromBox(block.bbox));
  }
  return found.size;
}

function resultReady(body: Record<string, unknown>): boolean {
  return "result" in body && body.result != null;
}

function reportedModel(body: Record<string, unknown>, requested: string): string {
  if (typeof body.model === "string" && body.model.trim()) return body.model.trim();
  if (isRecord(body.settings) && typeof body.settings.model === "string" && body.settings.model.trim()) {
    return body.settings.model.trim();
  }
  if (requested.trim()) return requested.trim();
  return DEFAULT_MODEL;
}

function modelOf(options: Record<string, unknown>): string {
  if (typeof options.model === "string" && options.model.trim()) return options.model.trim();
  if (isRecord(options.settings) && typeof options.settings.model === "string" && options.settings.model.trim()) {
    return options.settings.model.trim();
  }
  return DEFAULT_MODEL;
}

function jobIdOf(body: Record<string, unknown>): string {
  for (const key of ["job_id", "jobId"]) {
    const value = body[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  if (isRecord(body.job) && typeof body.job.id === "string" && body.job.id.trim()) return body.job.id.trim();
  return "";
}

function statusOf(body: Record<string, unknown>): string {
  if (typeof body.status !== "string") return "";
  return [...body.status.trim().toUpperCase()].filter((char) => char !== " " && char !== "_").join("");
}

function errorMessage(body: Record<string, unknown>): string {
  for (const key of ["error", "error_message", "message", "detail"]) {
    const value = body[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "conversion failed";
}

async function loadSchema(
  options: Record<string, unknown>,
  source: string,
): Promise<Record<string, unknown> | undefined> {
  const raw = schemaOption(options);
  if (raw === undefined) {
    const present = "extract_schema" in options ? options.extract_schema : options.extractSchema;
    if (present !== undefined) {
      throw new AdapterError("options.extract_schema must be a path to a JSON Schema file");
    }
    return undefined;
  }
  const schemaPath = await findSchema(raw.trim(), source);
  let loaded: unknown;
  try {
    loaded = JSON.parse(await readFile(schemaPath, "utf8")) as unknown;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new AdapterError(`reducto cannot read extract schema ${schemaPath}: ${message}`);
  }
  if (!isRecord(loaded)) throw new AdapterError(`reducto extract schema ${schemaPath} must be a JSON object`);
  return loaded;
}

function schemaOption(options: Record<string, unknown>): string | undefined {
  const raw = "extract_schema" in options ? options.extract_schema : options.extractSchema;
  if (typeof raw === "string" && raw.trim()) return raw;
  return undefined;
}

async function findSchema(candidate: string, source: string): Promise<string> {
  if (path.isAbsolute(candidate)) {
    if (await isFile(candidate)) return candidate;
    throw new AdapterError(`reducto cannot read extract schema ${candidate}: path not found`);
  }
  const cwdHit = path.resolve(candidate);
  const beside = path.resolve(path.dirname(source), candidate);
  if (await isFile(cwdHit)) return cwdHit;
  if (await isFile(beside)) return beside;
  throw new AdapterError(
    `reducto cannot read extract schema ${candidate}: path not found. Looked in ${cwdHit} and ${beside}.`,
  );
}

function apiKey(): string {
  return (process.env.REDUCTO_API_KEY ?? "").trim();
}

function baseUrl(options: Record<string, unknown>): string {
  for (const key of ["baseUrl", "base_url"]) {
    const value = options[key];
    if (typeof value === "string" && value.trim()) return stripTrailingSlashes(value.trim());
  }
  const env = (process.env.REDUCTO_BASE_URL ?? "").trim();
  if (env) return stripTrailingSlashes(env);
  return DEFAULT_BASE;
}

function timeoutSeconds(options: Record<string, unknown>): number {
  const value = "timeout_s" in options ? options.timeout_s : "timeoutS" in options ? options.timeoutS : 600;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new AdapterError("options.timeout_s must be a number greater than 0");
  }
  return value;
}

function publicOptions(options: Record<string, unknown>): Record<string, unknown> {
  const copy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(options)) if (!RESERVED.has(key)) copy[key] = value;
  return copy;
}

function picked(options: Record<string, unknown>, snake: string, camel: string): unknown {
  if (snake in options) return options[snake] ?? null;
  if (camel in options) return options[camel] ?? null;
  return null;
}

function pageFromBox(value: unknown): number {
  if (!isRecord(value)) return 1;
  return pageNumber(value.original_page) ?? pageNumber(value.page) ?? 1;
}

function pageNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 1 || Math.floor(value) !== value) {
    return undefined;
  }
  return value;
}

function rawBox(value: unknown): [number, number, number, number] | undefined {
  if (!isRecord(value)) return undefined;
  const left = asNumber(value.left);
  const top = asNumber(value.top);
  const width = asNumber(value.width);
  const height = asNumber(value.height);
  if (left === undefined || top === undefined || width === undefined || height === undefined) return undefined;
  return [left, top, width, height];
}

function bboxOf(value: unknown, width: number | undefined, height: number | undefined): BBox | undefined {
  const raw = rawBox(value);
  if (!raw || width === undefined || height === undefined || width <= 0 || height <= 0) return undefined;
  let x0 = raw[0] * width;
  let y0 = raw[1] * height;
  let x1 = (raw[0] + raw[2]) * width;
  let y1 = (raw[1] + raw[3]) * height;
  if (x0 > x1) [x0, x1] = [x1, x0];
  if (y0 > y1) [y0, y1] = [y1, y0];
  return [round4(x0), round4(y0), round4(x1), round4(y1)];
}

function positive(value: unknown): number | undefined {
  const number = asNumber(value);
  if (number === undefined || number <= 0) return undefined;
  return number;
}

function unit(value: unknown): number | undefined {
  const number = asNumber(value);
  if (number === undefined || number < 0 || number > 1) return undefined;
  return number;
}

function asNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

function round4(value: number): number {
  return Math.floor(value * 10000 + 0.5) / 10000;
}

function hashLevel(text: string): number {
  let index = 0;
  while (index < text.length && (text[index] === " " || text[index] === "\t")) index += 1;
  let hashes = 0;
  while (index < text.length && text[index] === "#" && hashes < 6) {
    hashes += 1;
    index += 1;
  }
  if (hashes >= 1 && (index === text.length || text[index] === " ")) return hashes;
  return 1;
}

function trim(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && SPACE.has(text[start] ?? "")) start += 1;
  while (end > start && SPACE.has(text[end - 1] ?? "")) end -= 1;
  return text.slice(start, end);
}

function snippet(text: string): string {
  const flat = [...text].map((char) => (char === "\r" || char === "\n" || char === "\t" ? " " : char)).join("");
  return flat.length > 200 ? flat.slice(0, 200) : flat;
}

function stripTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") end -= 1;
  return value.slice(0, end);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function assertFile(filePath: string): Promise<void> {
  try {
    const info = await stat(filePath);
    if (!info.isFile()) throw new AdapterError(`reducto cannot read ${filePath}: path not found`);
  } catch (err) {
    if (err instanceof AdapterError) throw err;
    throw new AdapterError(`reducto cannot read ${filePath}: path not found`);
  }
}

async function isFile(filePath: string): Promise<boolean> {
  try {
    return (await stat(filePath)).isFile();
  } catch {
    return false;
  }
}
