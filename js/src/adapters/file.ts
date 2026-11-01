/**
 * Score pre-parsed Document JSON or Markdown. No network and no API key.
 *
 * Looks in `options.dir` for `<golden id>.json`, then `<golden id>.md`.
 * The golden id is the stem of the path passed to `fetch`.
 *
 * @example
 * ```ts
 * import { FileAdapter } from "./adapters/file.js";
 *
 * const adapter = new FileAdapter(null, { dir: "out/parsed" });
 * const raw = adapter.fetch("files/invoice-acme.pdf");
 * ```
 */

import { readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020, type AnySchema, type ErrorObject } from "ajv/dist/2020.js";

import { AdapterError, type Adapter, type RawResult } from "./base.js";
import { packageVersion } from "./runner_support.js";
import type { Block, Document, Page, Source, Table } from "../models/document.js";

let validateDocument: ((data: unknown) => boolean) | null = null;
let documentErrors: () => ErrorObject[] = () => [];

/** Read parser output a pipeline already wrote to disk. */
export class FileAdapter implements Adapter {
  readonly id = "file";
  readonly variants: readonly string[] = [];
  /** False because the output is already on disk. */
  readonly cacheable = false;
  readonly variant: string | null;
  readonly directory: string;
  readonly options: Record<string, unknown>;

  /**
   * @example
   * ```ts
   * new FileAdapter(null, { dir: "out/parsed" }).cacheIdentity();
   * ```
   */
  constructor(variant: string | null, options: Record<string, unknown>) {
    this.variant = variant;
    this.options = options;
    this.directory = typeof options.dir === "string" ? options.dir : "";
  }

  /**
   * Return whether `options.dir` is set.
   *
   * @example
   * ```ts
   * await new FileAdapter(null, {}).available();
   * ```
   */
  available(): Promise<{ ok: boolean; reason?: string }> {
    if (!this.directory) {
      return Promise.resolve({
        ok: false,
        reason: "file adapter requires options.dir, a folder of <golden id>.json or .md files",
      });
    }
    return Promise.resolve({ ok: true, reason: "" });
  }

  /**
   * Return `{dir}`. The file adapter is not cached.
   *
   * @example
   * ```ts
   * new FileAdapter(null, { dir: "out/parsed" }).cacheIdentity();
   * ```
   */
  cacheIdentity(): Record<string, unknown> {
    return { dir: this.directory };
  }

  /**
   * Load `<dir>/<path stem>.json` or, if that is missing, the `.md` file.
   *
   * @example
   * ```ts
   * await new FileAdapter(null, { dir: "out" }).fetch("files/example.pdf");
   * ```
   */
  async fetch(filePath: string): Promise<RawResult> {
    if (!this.directory) {
      throw new AdapterError(
        "file adapter requires options.dir, a folder of <golden id>.json or .md files",
      );
    }
    const goldenId = path.basename(filePath, path.extname(filePath));
    const jsonPath = path.join(this.directory, `${goldenId}.json`);
    const markdownPath = path.join(this.directory, `${goldenId}.md`);
    if (isFile(jsonPath)) return readJson(jsonPath);
    if (isFile(markdownPath)) {
      return {
        payload: { markdown: readText(markdownPath) },
        parserVersion: "file",
        latencyMs: 0,
        costUsd: 0,
      };
    }
    throw new AdapterError(`no output for ${goldenId} in ${this.directory}`);
  }

  /**
   * Turn fetched JSON or Markdown into a Document.
   *
   * JSON is validated against `document.schema.json` and used as-is, except
   * `source` is the source the runner computed.
   *
   * @example
   * ```ts
   * adapter.toDocument(raw, source);
   * ```
   */
  toDocument(raw: RawResult, source: Source): Document {
    const payload = raw.payload;
    if (isRecord(payload) && "specVersion" in payload) {
      requireValid(payload, "output");
      const document = payload as unknown as Document;
      return { ...document, source };
    }
    if (isRecord(payload) && typeof payload.markdown === "string") {
      return markdownToDocument(payload.markdown, source);
    }
    throw new AdapterError("file adapter payload must be a Document or markdown text");
  }
}

/**
 * Convert a small Markdown subset to a Document.
 *
 * ATX headings become `heading` blocks. Other text becomes `paragraph`.
 * `-`, `*`, `+`, and `1.` items become `list_item`. A GFM pipe table uses the
 * first row as `header` and skips the alignment row. `<!-- page: N -->` sets
 * the page; otherwise the page is 1.
 *
 * @example
 * ```ts
 * markdownToDocument("# Title\n", source);
 * ```
 */
export function markdownToDocument(text: string, source: Source): Document {
  const { blocks, tables, highest } = parseMarkdown(text);
  const numbered = blocks.map((block, index) => ({ ...block, id: `b${index + 1}` }));
  const pages: Page[] = [];
  for (let number = 1; number <= highest; number += 1) pages.push({ number });
  return {
    specVersion: "1.0.0",
    source,
    meta: { parser: "file", parserVersion: "file", adapterVersion: packageVersion() },
    pages,
    blocks: numbered,
    tables,
  };
}

function parseMarkdown(text: string): { blocks: Block[]; tables: Table[]; highest: number } {
  const lines = splitLines(text);
  const blocks: Block[] = [];
  const tables: Table[] = [];
  const paragraph: string[] = [];
  let page = 1;
  let highest = 1;
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (isBlank(line)) {
      flushParagraph(paragraph, blocks, page);
      index += 1;
      continue;
    }
    const marker = pageMarker(line);
    if (marker !== null) {
      flushParagraph(paragraph, blocks, page);
      page = marker;
      highest = Math.max(highest, page);
      index += 1;
      continue;
    }
    const heading = headingOf(line);
    if (heading !== null) {
      flushParagraph(paragraph, blocks, page);
      blocks.push(block("heading", heading.body, page, heading.level));
      index += 1;
      continue;
    }
    const item = listItem(line);
    if (item !== null) {
      flushParagraph(paragraph, blocks, page);
      blocks.push(block("list_item", item, page));
      index += 1;
      continue;
    }
    if (startsTable(lines, index)) {
      flushParagraph(paragraph, blocks, page);
      const parsed = parseTable(lines, index, page, tables.length + 1);
      tables.push(parsed.table);
      index = parsed.next;
      continue;
    }
    paragraph.push(trim(line));
    index += 1;
  }
  flushParagraph(paragraph, blocks, page);
  return { blocks, tables, highest };
}

function flushParagraph(paragraph: string[], blocks: Block[], page: number): void {
  if (paragraph.length === 0) return;
  const text = paragraph.filter((part) => part.length > 0).join(" ");
  paragraph.length = 0;
  if (text) blocks.push(block("paragraph", text, page));
}

function block(type: Block["type"], text: string, page: number, level?: number): Block {
  const result: Block = { id: "pending", type, text, page };
  if (level !== undefined) result.level = level;
  return result;
}

function parseTable(
  lines: readonly string[],
  index: number,
  page: number,
  number: number,
): { table: Table; next: number } {
  const header = splitRow(lines[index] ?? "") ?? [];
  let cursor = index + 2;
  const rows: string[][] = [];
  let width = header.length;
  while (cursor < lines.length) {
    const line = lines[cursor] ?? "";
    const cells = splitRow(line);
    if (cells === null || isBlank(line)) break;
    if (isAlignment(line)) {
      cursor += 1;
      continue;
    }
    width = Math.max(width, cells.length);
    rows.push(cells);
    cursor += 1;
  }
  return {
    table: {
      id: `t${number}`,
      page,
      header: pad(header, width),
      rows: rows.map((row) => pad(row, width)),
    },
    next: cursor,
  };
}

function startsTable(lines: readonly string[], index: number): boolean {
  if (splitRow(lines[index] ?? "") === null) return false;
  if (index + 1 >= lines.length) return false;
  return isAlignment(lines[index + 1] ?? "");
}

function splitRow(line: string): string[] | null {
  const text = trim(line);
  if (!text.startsWith("|")) return null;
  const cells: string[] = [];
  let current = "";
  let index = 1;
  while (index < text.length) {
    const char = text[index] ?? "";
    if (char === "\\" && text[index + 1] === "|") {
      current += "|";
      index += 2;
      continue;
    }
    if (char === "|") {
      cells.push(trim(current));
      current = "";
      index += 1;
      continue;
    }
    current += char;
    index += 1;
  }
  if (!text.endsWith("|")) cells.push(trim(current));
  return cells;
}

function isAlignment(line: string): boolean {
  const cells = splitRow(line);
  if (cells === null || cells.length === 0) return false;
  for (const cell of cells) {
    let body = cell;
    if (body.startsWith(":")) body = body.slice(1);
    if (body.endsWith(":")) body = body.slice(0, -1);
    if (body.length === 0 || [...body].some((char) => char !== "-")) return false;
  }
  return true;
}

function pad(cells: string[], width: number): string[] {
  if (cells.length >= width) return cells;
  return [...cells, ...Array.from({ length: width - cells.length }, () => "")];
}

function headingOf(line: string): { level: number; body: string } | null {
  const text = trim(line);
  let level = 0;
  while (level < text.length && text[level] === "#") level += 1;
  if (level < 1 || level > 6 || level >= text.length || text[level] !== " ") return null;
  const body = stripClosingHashes(trim(text.slice(level + 1)));
  if (!body) return null;
  return { level, body };
}

function stripClosingHashes(text: string): string {
  let end = text.length;
  while (end > 0 && text[end - 1] === "#") end -= 1;
  if (end < text.length && end > 0 && text[end - 1] === " ") return trim(text.slice(0, end));
  return text;
}

function listItem(line: string): string | null {
  const text = trimIndent(line);
  for (const marker of ["- ", "* ", "+ "]) {
    if (text.startsWith(marker)) return trim(text.slice(marker.length));
  }
  let digits = 0;
  while (digits < text.length && isDigit(text[digits] ?? "")) digits += 1;
  if (digits === 0 || digits + 1 >= text.length) return null;
  const marker = text[digits];
  if ((marker === "." || marker === ")") && text[digits + 1] === " ") {
    return trim(text.slice(digits + 2));
  }
  return null;
}

function pageMarker(line: string): number | null {
  const text = trim(line);
  if (!text.startsWith("<!--") || !text.endsWith("-->")) return null;
  const inner = trim(text.slice(4, -3));
  if (!inner.startsWith("page:")) return null;
  const number = trim(inner.slice(5));
  if (!number || [...number].some((char) => !isDigit(char))) return null;
  const value = Number(number);
  if (value < 1) return null;
  return value;
}

function readJson(filePath: string): RawResult {
  let payload: unknown;
  try {
    payload = JSON.parse(readText(filePath)) as unknown;
  } catch (err) {
    if (err instanceof AdapterError) throw err;
    const detail = err instanceof SyntaxError ? err.message : "invalid JSON";
    throw new AdapterError(`output ${display(filePath)} is not valid JSON: ${detail}`);
  }
  if (!isRecord(payload)) {
    throw new AdapterError(
      `output ${display(filePath)} does not match document.schema.json: must be object`,
    );
  }
  requireValid(payload, display(filePath));
  return { payload, parserVersion: "file", latencyMs: 0, costUsd: 0 };
}

function requireValid(payload: Record<string, unknown>, label: string): void {
  const validate = documentValidator();
  if (validate(payload)) return;
  const error = documentErrors()[0];
  const location =
    error === undefined || error.instancePath === "" ? "<root>" : error.instancePath.slice(1);
  const message = error?.message ?? "invalid document";
  throw new AdapterError(
    `output ${label} does not match document.schema.json: ${location}: ${message}`,
  );
}

function documentValidator(): (data: unknown) => boolean {
  if (validateDocument === null) {
    const schemaPath = findSchema();
    const loaded: unknown = JSON.parse(readFileSync(schemaPath, "utf8"));
    if (!isRecord(loaded)) throw new AdapterError(`${schemaPath} must be a JSON object`);
    const ajv = new Ajv2020({ allErrors: true, strict: false, logger: false });
    const validate = ajv.compile(loaded as AnySchema);
    validateDocument = (data: unknown) => validate(data) === true;
    documentErrors = () => validate.errors ?? [];
  }
  return validateDocument;
}

function findSchema(): string {
  let dir = path.dirname(moduleFilename());
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = path.join(dir, "spec", "document.schema.json");
    try {
      if (readFileSync(candidate)) return candidate;
    } catch {
      // Keep walking toward the repository root.
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new AdapterError("could not find spec/document.schema.json; run from a docassay checkout");
}

declare const __filename: string | undefined;

function moduleFilename(): string {
  const url = import.meta.url;
  if (typeof url === "string" && url.length > 0) return fileURLToPath(url);
  // CJS bundles compile `import.meta` to an empty object. Node still passes
  // `__filename` into that module wrapper.
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  return process.argv[1] ?? process.cwd();
}

function readText(filePath: string): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(filePath));
  } catch (err) {
    if (err instanceof TypeError) {
      throw new AdapterError(`output ${display(filePath)} is not valid UTF-8`);
    }
    const detail = err instanceof Error ? err.message : "could not read file";
    throw new AdapterError(`output ${display(filePath)} could not be read: ${detail}`);
  }
}

function isFile(filePath: string): boolean {
  try {
    return statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function display(filePath: string): string {
  return filePath.split(path.sep).join("/");
}

function splitLines(text: string): string[] {
  return text.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
}

function isBlank(line: string): boolean {
  return trim(line) === "";
}

function trim(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && (text[start] === " " || text[start] === "\t")) start += 1;
  while (end > start && (text[end - 1] === " " || text[end - 1] === "\t")) end -= 1;
  return text.slice(start, end);
}

function trimIndent(text: string): string {
  let index = 0;
  while (index < text.length && (text[index] === " " || text[index] === "\t")) index += 1;
  return text.slice(index);
}

function isDigit(char: string): boolean {
  return char >= "0" && char <= "9";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
