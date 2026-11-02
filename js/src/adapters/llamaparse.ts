/**
 * LlamaParse adapter.
 *
 * Both languages call the LlamaParse v2 REST API (upload, poll, fetch items).
 * Mapping is pure. `parserVersion` is the version the API reports, and the
 * dated pin when the response omits one.
 *
 * @example
 * ```ts
 * import { LlamaParseAdapter } from "./adapters/llamaparse.js";
 *
 * const adapter = new LlamaParseAdapter("agentic", { version: "latest" });
 * const check = await adapter.available();
 * ```
 */

import { readFile, stat } from "node:fs/promises";
import path from "node:path";

import { AdapterError, type Adapter, type RawResult } from "./base.js";
import { packageVersion } from "./runner_support.js";
import type { BBox, Block, Document, Page, Source, Table } from "../models/document.js";

/**
 * LlamaParse v2 credit prices.
 * Source: https://developers.llamaindex.ai/llamaparse/general/pricing/
 * Read 2026-11-02. Conversion is $1.25 per 1,000 credits.
 */
export const CREDITS_PER_PAGE: Record<string, number> = {
  fast: 1,
  cost_effective: 3,
  agentic: 10,
  agentic_plus: 45,
};
export const USD_PER_THOUSAND_CREDITS = 1.25;

const TIERS = ["fast", "cost_effective", "agentic", "agentic_plus"] as const;
export const API_KEY =
  "LLAMA_CLOUD_API_KEY is not set. Create a key at https://cloud.llamaindex.ai and export it.";
const DEFAULT_BASE = "https://api.cloud.llamaindex.ai";
const EXPAND = "items,markdown,job_metadata";
const RESERVED = new Set(["baseUrl", "base_url", "timeout_s", "timeoutS", "apiKey", "api_key", "version"]);
const SPACE = new Set([" ", "\t", "\r", "\n", "\u00a0"]);
const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

/** Clock used while polling. Tests replace these to avoid waiting. */
export const pollControl = {
  now: (): number => Date.now() / 1000,
  sleep: (seconds: number): Promise<void> =>
    new Promise((resolve) => {
      setTimeout(resolve, seconds * 1000);
    }),
};

interface MapState {
  blocks: Block[];
  tables: Table[];
  usedPages: Set<number>;
}

/** Upload a file to LlamaParse and map the items tree to a Document. */
export class LlamaParseAdapter implements Adapter {
  readonly id = "llamaparse";
  readonly variants: readonly string[] = TIERS;
  readonly variant: string | null;
  readonly options: Record<string, unknown>;

  /**
   * @example
   * ```ts
   * new LlamaParseAdapter("fast", {});
   * ```
   */
  constructor(variant: string | null, options: Record<string, unknown>) {
    this.variant = variant;
    this.options = options;
  }

  /**
   * Return whether `LLAMA_CLOUD_API_KEY` is set.
   *
   * @example
   * ```ts
   * const check = await new LlamaParseAdapter("fast", {}).available();
   * ```
   */
  available(): Promise<{ ok: boolean; reason?: string }> {
    if (!apiKey()) return Promise.resolve({ ok: false, reason: API_KEY });
    return Promise.resolve({ ok: true });
  }

  /**
   * Return the tier, version, and options that change parser output.
   *
   * The API key, base URL, and timeout are omitted.
   *
   * @example
   * ```ts
   * new LlamaParseAdapter("agentic", { version: "2026-09-29" }).cacheIdentity();
   * ```
   */
  cacheIdentity(): Record<string, unknown> {
    return {
      variant: this.variant,
      version: versionOption(this.options),
      options: publicOptions(this.options),
    };
  }

  /**
   * Upload `filePath`, poll until the job finishes, and return the JSON body.
   *
   * @example
   * ```ts
   * const raw = await new LlamaParseAdapter("fast", {}).fetch("files/sample.pdf");
   * ```
   */
  async fetch(filePath: string): Promise<RawResult> {
    if (!this.variant || !TIERS.includes(this.variant as (typeof TIERS)[number])) {
      throw new AdapterError(
        "llamaparse needs a tier variant (fast, cost_effective, agentic, agentic_plus). Use an id like \"llamaparse:agentic\".",
      );
    }
    await assertFile(filePath);
    const key = apiKey();
    if (!key) throw new AdapterError(API_KEY);
    const timeoutS = timeoutSeconds(this.options);
    const version = versionOption(this.options);
    const base = baseUrl(this.options);
    const started = pollControl.now();
    const jobId = await upload(filePath, key, base, this.variant, this.options);
    const payload = await poll(filePath, key, base, jobId, timeoutS);
    const elapsed = (pollControl.now() - started) * 1000;
    return {
      payload,
      parserVersion: reportedVersion(payload, version),
      latencyMs: elapsed,
      costUsd: llamaparseCostUsd(this.variant, pageCount(payload)),
    };
  }

  /**
   * Map a recorded LlamaParse result. This method does not call the API.
   *
   * @example
   * ```ts
   * const document = adapter.toDocument(raw, source);
   * ```
   */
  toDocument(raw: RawResult, source: Source): Document {
    return llamaparseToDocument(raw.payload, source, raw.parserVersion, this.variant);
  }
}

/**
 * Return USD for `pageCount` pages at `tier`.
 *
 * One `agentic` page is 10 credits. The credit table is `CREDITS_PER_PAGE`.
 *
 * @example
 * ```ts
 * llamaparseCostUsd("agentic", 1);
 * ```
 */
export function llamaparseCostUsd(tier: string, pages: number): number {
  if (typeof pages === "boolean" || typeof pages !== "number" || !Number.isInteger(pages) || pages < 1) {
    return 0;
  }
  const credits = CREDITS_PER_PAGE[tier];
  if (credits === undefined) return 0;
  return (credits * pages * USD_PER_THOUSAND_CREDITS) / 1000;
}

/**
 * Map one LlamaParse v2 result (`expand=items,markdown`) to a Document.
 *
 * Item boxes are top-left page points. A box whose coordinates all lie in
 * 0–1 is scaled by the page size, and omitted when that size is missing.
 * The first table row is the header. HTML `colspan` and `rowspan` repeat
 * the cell text. Header and footer containers stay `header` and `footer`.
 *
 * @example
 * ```ts
 * llamaparseToDocument({ items: { pages: [] } }, source, "2026-09-29", "fast");
 * ```
 */
export function llamaparseToDocument(
  payload: unknown,
  source: Source,
  parserVersion: string,
  variant: string | null,
): Document {
  if (!isRecord(payload)) {
    throw new AdapterError("llamaparse payload must be a JSON object from GET /api/v2/parse/{job_id}");
  }
  if (!parserVersion) {
    throw new AdapterError("llamaparse parserVersion is empty. Record the version the API reported.");
  }
  const pagesRaw = record(payload.items).pages;
  if (!Array.isArray(pagesRaw)) {
    throw new AdapterError("llamaparse payload needs items.pages. Fetch the job with expand=items.");
  }
  const regions = markdownRegions(payload);
  const sizes = new Map<number, { width?: number; height?: number }>();
  const state: MapState = { blocks: [], tables: [], usedPages: new Set() };
  for (const page of pagesRaw) {
    if (!isRecord(page)) continue;
    const number = pageNumber(page.page_number);
    const width = positive(page.page_width);
    const height = positive(page.page_height);
    const size: { width?: number; height?: number } = {};
    if (width !== undefined) size.width = width;
    if (height !== undefined) size.height = height;
    sizes.set(number, size);
    const items = Array.isArray(page.items) ? page.items : [];
    const failed = page.success === false;
    if (!hasRegion(items, "header") && regions.headers.has(number)) {
      addBlock(state, "header", regions.headers.get(number) ?? "", number, undefined, undefined);
    }
    if (!failed) {
      for (const item of items) emit(item, number, width, height, state, undefined, false);
    }
    if (!hasRegion(items, "footer") && regions.footers.has(number)) {
      addBlock(state, "footer", regions.footers.get(number) ?? "", number, undefined, undefined);
    }
  }
  for (const number of state.usedPages) {
    if (!sizes.has(number)) sizes.set(number, {});
  }
  const pages: Page[] = [...sizes.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([number, size]) => {
      const entry: Page = { number };
      if (size.width !== undefined) entry.width = size.width;
      if (size.height !== undefined) entry.height = size.height;
      return entry;
    });
  return {
    specVersion: "1.0.0",
    source,
    meta: {
      parser: variant ? `llamaparse:${variant}` : "llamaparse",
      parserVersion,
      adapterVersion: packageVersion(),
    },
    pages,
    blocks: state.blocks,
    tables: state.tables,
  };
}

async function upload(
  filePath: string,
  key: string,
  base: string,
  variant: string,
  options: Record<string, unknown>,
): Promise<string> {
  const config: Record<string, unknown> = { tier: variant, version: versionOption(options) };
  const extras = publicOptions(options);
  for (const name of Object.keys(extras).sort()) config[name] = extras[name];
  const bytes = await readFile(filePath);
  const form = new FormData();
  form.append("file", new Blob([bytes]), path.basename(filePath));
  form.append("configuration", JSON.stringify(config));
  const body = await exchange(filePath, "POST", `${base}/api/v2/parse/upload`, key, form);
  const jobId = jobIdOf(body);
  if (!jobId) throw new AdapterError(`llamaparse upload for ${filePath} did not return a job id`);
  return jobId;
}

async function poll(
  filePath: string,
  key: string,
  base: string,
  jobId: string,
  timeoutS: number,
): Promise<Record<string, unknown>> {
  const url = `${base}/api/v2/parse/${encodeURIComponent(jobId)}?expand=${EXPAND}`;
  let delay = 1;
  const started = pollControl.now();
  for (;;) {
    const body = await exchange(filePath, "GET", url, key);
    const job = jobRecord(body);
    const label = statusOf(job);
    if (label === "COMPLETED") return body;
    if (label === "FAILED" || label === "CANCELLED") {
      throw new AdapterError(`llamaparse job ${jobId} ${label} on ${filePath}: ${errorMessage(body, job)}`);
    }
    const remaining = timeoutS - (pollControl.now() - started);
    if (remaining <= 0) {
      throw new AdapterError(
        `llamaparse timed out after ${timeoutS}s waiting for job ${jobId} on ${filePath}. Raise options.timeout_s or retry the file.`,
      );
    }
    await pollControl.sleep(delay < remaining ? delay : remaining);
    delay = delay * 2 > 8 ? 8 : delay * 2;
  }
}

async function exchange(
  filePath: string,
  method: string,
  url: string,
  key: string,
  form?: FormData,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${key}` },
      body: form,
      signal: AbortSignal.timeout(60_000),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new AdapterError(`llamaparse failed on ${filePath}: ${message}`);
  }
  const text = await response.text();
  if (response.status < 200 || response.status >= 300) {
    throw new AdapterError(
      `llamaparse returned HTTP ${response.status} while ${method === "POST" ? "uploading" : "polling"} ${filePath}: ${snippet(text)}. Check the tier, version, and LLAMA_CLOUD_API_KEY.`,
    );
  }
  let body: unknown;
  try {
    body = JSON.parse(text) as unknown;
  } catch {
    throw new AdapterError(
      `llamaparse returned a non-JSON body for ${filePath}. Check LLAMA_CLOUD_BASE_URL and the API status.`,
    );
  }
  if (!isRecord(body)) throw new AdapterError(`llamaparse returned a non-object body for ${filePath}`);
  return body;
}

function emit(
  item: unknown,
  page: number,
  width: number | undefined,
  height: number | undefined,
  state: MapState,
  force: Block["type"] | undefined,
  inList: boolean,
): void {
  if (!isRecord(item)) return;
  const kind = typeof item.type === "string" ? item.type : "";
  if (kind === "table") {
    addTable(item, page, width, height, state);
    return;
  }
  if (kind === "list") {
    if (Array.isArray(item.items)) {
      for (const child of item.items) emit(child, page, width, height, state, force, true);
    }
    return;
  }
  if (kind === "header" || kind === "footer") {
    const forced = kind === "header" ? "header" : "footer";
    if (Array.isArray(item.items) && item.items.some((child) => isRecord(child))) {
      for (const child of item.items) emit(child, page, width, height, state, forced, false);
      return;
    }
    const text = trim(typeof item.md === "string" ? item.md : "");
    if (text) addBlock(state, forced, text, page, undefined, bboxOf(item.bbox, width, height));
    return;
  }
  if (force !== undefined) {
    const text = itemText(item, kind);
    if (text) addBlock(state, force, text, page, undefined, bboxOf(item.bbox, width, height));
    return;
  }
  const classified = classify(item, kind, inList);
  if (classified.text) {
    addBlock(state, classified.type, classified.text, page, classified.level, bboxOf(item.bbox, width, height));
  }
}

function classify(
  item: Record<string, unknown>,
  kind: string,
  inList: boolean,
): { type: Block["type"]; text: string; level?: number } {
  if (kind === "heading") return { type: "heading", text: asString(item.value), level: headingLevel(item.level) };
  if (kind === "text") return { type: inList ? "list_item" : "paragraph", text: asString(item.value) };
  if (kind === "code") return { type: "code", text: asString(item.value) };
  if (kind === "image") {
    if (typeof item.caption === "string" && item.caption) return { type: "caption", text: item.caption };
    return { type: "other", text: asString(item.md) };
  }
  if (kind === "link") {
    if (typeof item.text === "string" && item.text) return { type: "other", text: item.text };
    return { type: "other", text: asString(item.md) };
  }
  return { type: "other", text: itemText(item, kind) };
}

function addTable(
  item: Record<string, unknown>,
  page: number,
  width: number | undefined,
  height: number | undefined,
  state: MapState,
): void {
  const matrix = tableMatrix(item);
  if (matrix.length === 0) return;
  const header = matrix[0] ?? [];
  const rows = matrix.slice(1);
  const box = bboxOf(item.bbox, width, height);
  const markdown = typeof item.md === "string" && item.md ? item.md : undefined;
  const table: Table = {
    id: `t${state.tables.length + 1}`,
    page,
    ...(box ? { bbox: box } : {}),
    header,
    rows,
    ...(markdown ? { markdown } : {}),
  };
  state.tables.push(table);
  state.usedPages.add(page);
}

function tableMatrix(item: Record<string, unknown>): string[][] {
  const html = typeof item.html === "string" ? item.html : "";
  const rows = matrixRows(item.rows);
  const lower = html.toLowerCase();
  const spanned = lower.includes("colspan=") || lower.includes("rowspan=");
  if (html && (spanned || rows.length === 0)) {
    const grid = htmlGrid(html);
    if (grid && (spanned || rows.length === 0)) return grid;
  }
  return rows;
}

function matrixRows(value: unknown): string[][] {
  if (!Array.isArray(value) || value.length === 0) return [];
  const raw: string[][] = [];
  let width = 0;
  for (const row of value) {
    if (!Array.isArray(row)) return [];
    const cells = row.map((entry) => cellText(entry));
    raw.push(cells);
    if (cells.length > width) width = cells.length;
  }
  return raw.map((row) => row.concat(Array.from({ length: width - row.length }, () => "")));
}

function cellText(value: unknown): string {
  if (value === null || value === undefined || typeof value === "boolean") return "";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "";
    if (Math.floor(value) === value && Math.abs(value) <= 2 ** 53) return String(Math.trunc(value));
    return String(value);
  }
  if (typeof value === "string") return value;
  return "";
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
    if (nameEnd < html.length && !" >/\t\r\n".includes(lower[nameEnd] ?? "")) {
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
    if (nameEnd < row.length && !" >/\t\r\n".includes(lower[nameEnd] ?? "")) {
      index = nameEnd;
      continue;
    }
    const tag = lower.slice(start + 1, start + 3);
    const gt = lower.indexOf(">", nameEnd);
    if (gt < 0) break;
    const attrs = lower.slice(nameEnd, gt);
    const colspan = attrInt(attrs, "colspan");
    const rowspan = attrInt(attrs, "rowspan");
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

function addBlock(
  state: MapState,
  type: Block["type"],
  text: string,
  page: number,
  level: number | undefined,
  box: BBox | undefined,
): void {
  const block: Block = {
    id: `b${state.blocks.length + 1}`,
    type,
    text,
    page,
    ...(level !== undefined ? { level } : {}),
    ...(box ? { bbox: box } : {}),
  };
  state.blocks.push(block);
  state.usedPages.add(page);
}

function itemText(item: Record<string, unknown>, kind: string): string {
  if (kind === "image") {
    if (typeof item.caption === "string" && item.caption) return item.caption;
    return asString(item.md);
  }
  if (kind === "link") {
    if (typeof item.text === "string" && item.text) return item.text;
    return asString(item.md);
  }
  if (typeof item.value === "string" && item.value) return item.value;
  return asString(item.md);
}

function bboxOf(value: unknown, width: number | undefined, height: number | undefined): BBox | undefined {
  if (!Array.isArray(value)) return undefined;
  const boxes: Array<[number, number, number, number]> = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const x = asNumber(item.x);
    const y = asNumber(item.y);
    const w = asNumber(item.w);
    const h = asNumber(item.h);
    if (x === undefined || y === undefined || w === undefined || h === undefined) continue;
    let x0 = x;
    let y0 = y;
    let x1 = x + w;
    let y1 = y + h;
    const largest = Math.max(Math.abs(x0), Math.abs(y0), Math.abs(w), Math.abs(h), Math.abs(x1), Math.abs(y1));
    if (largest <= 1) {
      if (width === undefined || height === undefined || width <= 1 || height <= 1) continue;
      x0 *= width;
      y0 *= height;
      x1 *= width;
      y1 *= height;
    }
    if (x0 > x1) [x0, x1] = [x1, x0];
    if (y0 > y1) [y0, y1] = [y1, y0];
    boxes.push([x0, y0, x1, y1]);
  }
  if (boxes.length === 0) return undefined;
  return [
    round4(Math.min(...boxes.map((box) => box[0]))),
    round4(Math.min(...boxes.map((box) => box[1]))),
    round4(Math.max(...boxes.map((box) => box[2]))),
    round4(Math.max(...boxes.map((box) => box[3]))),
  ];
}

function markdownRegions(payload: Record<string, unknown>): {
  headers: Map<number, string>;
  footers: Map<number, string>;
} {
  const headers = new Map<number, string>();
  const footers = new Map<number, string>();
  const pages = record(payload.markdown).pages;
  if (!Array.isArray(pages)) return { headers, footers };
  for (const page of pages) {
    if (!isRecord(page)) continue;
    const number = pageNumber(page.page_number);
    const header = trim(typeof page.header === "string" ? page.header : "");
    const footer = trim(typeof page.footer === "string" ? page.footer : "");
    if (header) headers.set(number, header);
    if (footer) footers.set(number, footer);
  }
  return { headers, footers };
}

function hasRegion(items: unknown[], kind: string): boolean {
  return items.some((item) => isRecord(item) && item.type === kind);
}

function reportedVersion(payload: Record<string, unknown>, requested: string): string {
  const found = versionField(payload);
  if (found) return found;
  if (requested && requested !== "latest") return requested;
  return "unknown";
}

function versionField(payload: Record<string, unknown>): string {
  const job = record(payload.job);
  const jobVersion = nonempty(job.version);
  if (jobVersion) return jobVersion;
  const jobParameters = nonempty(record(job.parameters).version);
  if (jobParameters) return jobParameters;
  const raw = record(payload.raw_parameters);
  const rawVersion = nonempty(raw.version);
  if (rawVersion) return rawVersion;
  const rawParameters = nonempty(record(raw.parameters).version);
  if (rawParameters) return rawParameters;
  const meta = record(payload.job_metadata);
  const metaVersion = nonempty(meta.version);
  if (metaVersion) return metaVersion;
  return nonempty(record(meta.parameters).version);
}

function pageCount(payload: Record<string, unknown>): number {
  const items = record(payload.items).pages;
  if (Array.isArray(items) && items.length > 0) return items.length;
  const pages = record(payload.markdown).pages;
  if (Array.isArray(pages)) return pages.length;
  return 0;
}

function jobIdOf(body: Record<string, unknown>): string {
  if (typeof body.id === "string" && body.id) return body.id;
  return nonempty(record(body.job).id);
}

function jobRecord(body: Record<string, unknown>): Record<string, unknown> {
  const job = body.job;
  if (isRecord(job) && Object.keys(job).length > 0) return job;
  return body;
}

function statusOf(job: Record<string, unknown>): string {
  return typeof job.status === "string" ? job.status.trim().toUpperCase() : "";
}

function errorMessage(body: Record<string, unknown>, job: Record<string, unknown>): string {
  for (const source of [job, body]) {
    const text = source.error_message;
    if (typeof text === "string" && text.trim()) return text.trim();
  }
  return "conversion failed";
}

function apiKey(): string {
  return (process.env.LLAMA_CLOUD_API_KEY ?? "").trim();
}

function baseUrl(options: Record<string, unknown>): string {
  for (const key of ["baseUrl", "base_url"]) {
    const value = options[key];
    if (typeof value === "string" && value.trim()) return stripTrailingSlashes(value.trim());
  }
  const env = (process.env.LLAMA_CLOUD_BASE_URL ?? "").trim();
  if (env) return stripTrailingSlashes(env);
  return DEFAULT_BASE;
}

function versionOption(options: Record<string, unknown>): string {
  const value = options.version;
  if (typeof value === "string" && value.trim()) return value.trim();
  return "latest";
}

function timeoutSeconds(options: Record<string, unknown>): number {
  const value = options.timeout_s ?? options.timeoutS ?? 600;
  if (typeof value === "boolean" || typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new AdapterError("options.timeout_s must be a number greater than 0");
  }
  return value;
}

function publicOptions(options: Record<string, unknown>): Record<string, unknown> {
  const copy: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(options)) {
    if (!RESERVED.has(key)) copy[key] = value;
  }
  return copy;
}

function pageNumber(value: unknown): number {
  if (typeof value === "boolean") return 1;
  if (typeof value === "number" && Number.isFinite(value) && value >= 1 && Math.floor(value) === value) {
    return value;
  }
  return 1;
}

function positive(value: unknown): number | undefined {
  const number = asNumber(value);
  if (number === undefined || number <= 0) return undefined;
  return number;
}

function round4(value: number): number {
  return Math.floor(value * 10000 + 0.5) / 10000;
}

function asNumber(value: unknown): number | undefined {
  if (typeof value === "boolean" || typeof value !== "number") return undefined;
  if (!Number.isFinite(value)) return undefined;
  return value;
}

function headingLevel(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) return 1;
  return value;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function stripTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") end -= 1;
  return value.slice(0, end);
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function nonempty(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

async function assertFile(filePath: string): Promise<void> {
  try {
    const info = await stat(filePath);
    if (!info.isFile()) throw new AdapterError(`llamaparse cannot read ${filePath}: path not found`);
  } catch (err) {
    if (err instanceof AdapterError) throw err;
    throw new AdapterError(`llamaparse cannot read ${filePath}: path not found`);
  }
}
