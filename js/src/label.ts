/**
 * Draft a golden file from a parser Document and review it locally.
 *
 * The draft is deterministic. Each drafted value ends with `# TODO verify`.
 * `docassay validate --strict` fails until the review page saves the file.
 *
 * @example
 * ```ts
 * import { draftYaml } from "./label.js";
 *
 * draftYaml(document, "../files/lease.pdf", "lease");
 * ```
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { parse } from "yaml";

import {
  AdapterConfigError,
  AdapterError,
  AdapterUnavailable,
  type Adapter,
} from "./adapters/base.js";
import { resolve } from "./adapters/registry.js";
import { ensureAvailable, runParser } from "./adapters/runner_support.js";
import { ConfigError, loadConfig } from "./config.js";
import type { Document, Table } from "./models/document.js";
import { isWhitespace } from "./normalize/text.js";

const SNIPPET = 80;
const TODO = " # TODO verify";

/** A golden object rendered to YAML. */
export interface GoldenDraft {
  file: string;
  id?: string;
  expect?: Record<string, unknown>;
}

/** A label command that cannot draft or open the review page. */
export class LabelError extends Error {
  readonly exitCode: number;

  /**
   * @example
   * ```ts
   * throw new LabelError("pass --from <parser> or --review");
   * ```
   */
  constructor(message: string, exitCode = 2) {
    super(message);
    this.name = "LabelError";
    this.exitCode = exitCode;
  }
}

/** Local review page bound to 127.0.0.1. */
export interface ReviewServer {
  /** Base URL of the review page. */
  url: string;
  /** Resolves when the reviewer saves. */
  done: Promise<void>;
  /** Stop the server if the page is still open. */
  close: () => Promise<void>;
}

/**
 * Return golden YAML for `document`.
 *
 * Up to three largest tables are kept. Each contributes its header, body-row
 * count, column count, and two cells nearest the middle of the middle body
 * row. Text spans are the block before a page break, the first footnote or
 * caption, and the last block on the last page.
 *
 * @example
 * ```ts
 * draftYaml(document, "../files/lease.pdf", "lease");
 * ```
 */
export function draftYaml(document: Document, fileRef: string, docId: string): string {
  return renderYaml(draftDict(document, fileRef, docId), true);
}

/**
 * Return `data` as golden YAML. `todo` marks every drafted value.
 *
 * @example
 * ```ts
 * renderYaml({ file: "a.pdf", id: "a" }, false);
 * ```
 */
export function renderYaml(data: GoldenDraft, todo: boolean): string {
  if (data.file === "") throw new LabelError("golden file is missing file. Add a file path and retry.");
  const lines = [`file: ${quote(data.file)}`];
  if (data.id !== undefined && data.id !== "") lines.push(`id: ${quote(data.id)}`);
  if (data.expect !== undefined && Object.keys(data.expect).length > 0) {
    lines.push("expect:", ...expectLines(data.expect, todo));
  }
  return `${lines.join("\n")}\n`;
}

/**
 * Draft a golden file or open the review page. Returns the exit code.
 *
 * @example
 * ```ts
 * await labelCommand("files/lease.pdf", { parser: "file" });
 * ```
 */
export async function labelCommand(
  filePath: string,
  options: {
    parser?: string;
    out?: string;
    review?: boolean;
    config?: string;
    openBrowser?: boolean;
  } = {},
): Promise<number> {
  try {
    if (options.review === true && options.parser !== undefined) {
      throw new LabelError("pass either --from <parser> or --review, not both.");
    }
    if (options.review === true) {
      const server = await startReview(filePath, options.openBrowser !== false);
      process.stdout.write(`review at ${server.url}\n`);
      await server.done;
      process.stdout.write(`wrote ${filePath.split(path.sep).join("/")}\n`);
      return 0;
    }
    if (options.parser === undefined) {
      throw new LabelError(
        "usage: docassay label <file> --from <parser> [--out golden/<id>.yaml]\n" +
          "       docassay label <golden.yaml> --review",
      );
    }
    const target = await writeDraft(
      filePath,
      options.parser,
      options.out,
      options.config ?? "docassay.yaml",
    );
    process.stdout.write(`wrote ${target.split(path.sep).join("/")}\n`);
    return 0;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    process.stderr.write(`${message}\n`);
    if (err instanceof LabelError) return err.exitCode;
    if (err instanceof AdapterUnavailable) return 3;
    if (err instanceof AdapterError) return 3;
    if (err instanceof AdapterConfigError || err instanceof ConfigError) return 2;
    throw err;
  }
}

/**
 * Serve the review page for `goldenPath` on 127.0.0.1.
 *
 * Saving rewrites the file without `# TODO verify`.
 *
 * @example
 * ```ts
 * const server = await startReview("golden/lease.yaml", false);
 * await server.close();
 * ```
 */
export function startReview(goldenPath: string, openBrowser = true): Promise<ReviewServer> {
  let resolved: string;
  let model: GoldenDraft;
  try {
    resolved = path.resolve(goldenPath);
    if (!existsSync(resolved)) {
      throw new LabelError(`${goldenPath}: golden file not found. Draft it with --from first.`);
    }
    model = loadModel(resolved);
  } catch (err) {
    return Promise.reject(err instanceof Error ? err : new LabelError(String(err)));
  }
  const html = reviewHtml(model);
  const source = sourcePath(resolved, model);
  let finish = (): void => undefined;
  const done = new Promise<void>((resolveDone) => {
    finish = resolveDone;
  });
  let settled = false;
  const complete = (): void => {
    if (settled) return;
    settled = true;
    finish();
  };
  const server = createServer((req, res) => {
    const url = (req.url ?? "/").split("?")[0] ?? "/";
    if (req.method === "GET" && url === "/") {
      send(res, 200, "text/html; charset=utf-8", html);
      return;
    }
    if (req.method === "GET" && url === "/source") {
      sendSource(res, source);
      return;
    }
    if (req.method === "GET" && url === "/shutdown") {
      send(res, 200, "text/plain; charset=utf-8", "bye\n");
      complete();
      server.close();
      return;
    }
    if (req.method === "POST" && url === "/save") {
      readBody(req)
        .then((raw) => {
          const text = confirmedYaml(JSON.parse(raw) as unknown);
          writeFileSync(resolved, text, "utf8");
          send(res, 200, "text/plain; charset=utf-8", "saved\n");
          complete();
          server.close();
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : String(err);
          send(res, 400, "text/plain; charset=utf-8", `${message}\n`);
        });
      return;
    }
    send(res, 404, "text/plain; charset=utf-8", "not found\n");
  });
  return new Promise((resolveServer, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeAllListeners("error");
      server.on("error", () => undefined);
      const address = server.address();
      const port = address !== null && typeof address !== "string" ? address.port : 0;
      const url = `http://127.0.0.1:${port}`;
      if (openBrowser) {
        void import("node:child_process").then(({ spawn }) => {
          const opener = process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
          spawn(opener, [url], { stdio: "ignore", detached: true }).unref();
        });
      }
      let closed = false;
      resolveServer({
        url,
        done,
        close: () =>
          new Promise((resolveClose) => {
            complete();
            if (closed) {
              resolveClose();
              return;
            }
            closed = true;
            server.close(() => resolveClose());
          }),
      });
    });
  });
}

/**
 * Return the review page for `model`.
 *
 * The source PDF is shown from `/source`. The page does not load a CDN.
 *
 * @example
 * ```ts
 * reviewHtml({ file: "a.pdf", id: "a" }).includes("<html");
 * ```
 */
export function reviewHtml(model: GoldenDraft): string {
  const payload = JSON.stringify(model).replaceAll("<", "\\u003c");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>docassay label</title>
<style>
body { margin: 0; font: 16px/1.4 sans-serif; color: #1a1a1a; }
main { display: flex; height: 100vh; }
.sheet, form { flex: 1; overflow: auto; padding: 16px; box-sizing: border-box; }
.sheet { background: #f4f4f4; }
iframe { width: 100%; height: calc(100% - 32px); border: 0; background: white; }
button { margin-left: 8px; }
label { display: block; margin: 8px 0; }
section { border-top: 1px solid #ccc; margin-top: 12px; padding-top: 8px; }
</style>
</head>
<body>
<main>
<section class="sheet">
<p>Source file</p>
<iframe src="/source" title="Source PDF"></iframe>
</section>
<form id="draft">
<p>Edit the draft, remove a wrong line, then save. Saving drops # TODO verify.</p>
<div id="fields"></div>
<p><button type="submit">Save</button> <span id="status"></span></p>
</form>
</main>
<script id="draft-data" type="application/json">${payload}</script>
<script>
const draft = JSON.parse(document.getElementById("draft-data").textContent);
const root = document.getElementById("fields");
const expect = draft.expect || {};
function item(html) {
  const node = document.createElement("div");
  node.dataset.item = "1";
  node.innerHTML = html + '<button type="button" data-remove>Remove</button>';
  return node;
}
function addSection(title, nodes) {
  if (!nodes.length) return;
  const section = document.createElement("section");
  section.innerHTML = "<h2>" + title + "</h2>";
  nodes.forEach((node) => section.appendChild(node));
  root.appendChild(section);
}
(expect.tables || []).forEach((table) => {
  const section = document.createElement("section");
  section.dataset.table = "1";
  section.innerHTML = "<h2>Table</h2>";
  section.appendChild(item('page <input data-page value="' + esc(table.page) + '">'));
  (table.header || []).forEach((cell) => {
    section.appendChild(item('header <input data-header value="' + esc(cell) + '">'));
  });
  section.appendChild(item('rows <input data-rows value="' + esc(table.rows) + '">'));
  section.appendChild(item('cols <input data-cols value="' + esc(table.cols) + '">'));
  (table.cells || []).forEach((cell) => {
    const node = item(
      'row <input data-row value="' + esc(cell.row) + '"> ' +
      'col <input data-col value="' + esc(cell.col) + '"> ' +
      'value <input data-value value="' + esc(cell.value) + '">'
    );
    node.dataset.cell = "1";
    section.appendChild(node);
  });
  section.insertAdjacentHTML("beforeend", '<button type="button" data-remove>Remove table</button>');
  root.appendChild(section);
});
addSection("Text", (expect.text_contains || []).map((text) =>
  item('text <input data-text value="' + esc(text) + '">')));
addSection("Reading order", (expect.reading_order || []).map((pair) =>
  item('before <input data-before value="' + esc(pair[0]) + '"> after <input data-after value="' + esc(pair[1]) + '">')));
const fieldNodes = Object.keys(expect.fields || {}).map((key) => {
  const value = expect.fields[key];
  const type = value === null ? "null" : typeof value;
  return item(esc(key) + ' <input data-field="' + esc(key) + '" data-type="' + type + '" value="' + esc(value) + '">');
});
addSection("Fields", fieldNodes);
root.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  const section = button.closest("[data-table]");
  if (button.parentElement === section) section.remove();
  else button.closest("[data-item]").remove();
});
document.getElementById("draft").addEventListener("submit", async (event) => {
  event.preventDefault();
  const next = { file: draft.file, id: draft.id, expect: {} };
  const tables = [];
  root.querySelectorAll("[data-table]").forEach((section) => {
    const table = { cells: [] };
    const page = section.querySelector("[data-page]");
    const rows = section.querySelector("[data-rows]");
    const cols = section.querySelector("[data-cols]");
    if (page) table.page = Number(page.value);
    if (rows) table.rows = Number(rows.value);
    if (cols) table.cols = Number(cols.value);
    const header = [];
    section.querySelectorAll("[data-header]").forEach((input) => header.push(input.value));
    if (header.length) table.header = header;
    section.querySelectorAll("[data-cell]").forEach((cell) => {
      const col = cell.querySelector("[data-col]").value;
      table.cells.push({
        row: Number(cell.querySelector("[data-row]").value),
        col: /^[0-9]+$/.test(col) ? Number(col) : col,
        value: cell.querySelector("[data-value]").value
      });
    });
    tables.push(table);
  });
  if (tables.length) next.expect.tables = tables;
  const texts = [];
  root.querySelectorAll("[data-text]").forEach((input) => { if (input.value) texts.push(input.value); });
  if (texts.length) next.expect.text_contains = texts;
  const pairs = [];
  root.querySelectorAll("[data-before]").forEach((input) => {
    const after = input.parentElement.querySelector("[data-after]").value;
    if (input.value && after) pairs.push([input.value, after]);
  });
  if (pairs.length) next.expect.reading_order = pairs;
  const fields = {};
  root.querySelectorAll("[data-field]").forEach((input) => {
    fields[input.dataset.field] = typed(input.dataset.type, input.value);
  });
  if (Object.keys(fields).length) next.expect.fields = fields;
  const response = await fetch("/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(next)
  });
  document.getElementById("status").textContent = response.ok
    ? "Saved. You can close this tab."
    : "Could not save. Check the values and retry.";
});
function typed(type, value) {
  if (type === "number") return Number(value);
  if (type === "boolean") return value === "true";
  if (type === "null") return null;
  return value;
}
function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
</script>
</body>
</html>
`;
}

function draftDict(document: Document, fileRef: string, docId: string): GoldenDraft {
  const expect: Record<string, unknown> = {};
  const texts = blockTexts(document);
  const tables = tableDrafts(document);
  const spans = textSpans(texts);
  const pairs = orderPairs(texts);
  const fields = fieldDrafts(document);
  if (tables.length > 0) expect.tables = tables;
  if (spans.length > 0) expect.text_contains = spans;
  if (pairs.length > 0) expect.reading_order = pairs;
  if (Object.keys(fields).length > 0) expect.fields = fields;
  const payload: GoldenDraft = { file: fileRef, id: docId };
  if (Object.keys(expect).length > 0) payload.expect = expect;
  return payload;
}

function tableDrafts(document: Document): Record<string, unknown>[] {
  const ranked = document.tables
    .map((table, index) => ({ table, index, size: tableSize(table) }))
    .filter((item) => item.size > 0)
    .sort((left, right) => right.size - left.size || left.index - right.index)
    .slice(0, 3);
  return ranked.map(({ table }) => {
    const header = table.header === undefined ? undefined : [...table.header];
    const width = tableWidth(header, table.rows);
    const entry: Record<string, unknown> = { page: table.page, rows: table.rows.length, cols: width };
    if (header !== undefined) entry.header = header;
    const cells: Record<string, unknown>[] = [];
    if (table.rows.length > 0 && width > 0) {
      const midRow = Math.floor(table.rows.length / 2);
      const row = table.rows[midRow] ?? [];
      for (const col of middleColumns(width)) {
        cells.push({
          row: midRow,
          col: columnLabel(header, col),
          value: row[col] ?? "",
        });
      }
    }
    if (cells.length > 0) entry.cells = cells;
    return entry;
  });
}

function tableSize(table: Table): number {
  return (table.header?.length ?? 0) + table.rows.reduce((sum, row) => sum + row.length, 0);
}

function tableWidth(header: readonly string[] | undefined, rows: readonly (readonly string[])[]): number {
  let width = header?.length ?? 0;
  for (const row of rows) width = Math.max(width, row.length);
  return width;
}

function middleColumns(width: number): number[] {
  const center = (width - 1) / 2;
  return [...Array(width).keys()]
    .sort((left, right) => Math.abs(left - center) - Math.abs(right - center) || left - right)
    .slice(0, 2)
    .sort((left, right) => left - right);
}

function columnLabel(header: readonly string[] | undefined, col: number): string | number {
  if (header === undefined || header[col] === undefined || header[col] === "") return col;
  const name = header[col] ?? "";
  if (header.filter((item) => item === name).length > 1) return col;
  return name;
}

function blockTexts(document: Document): Array<[number, string, string]> {
  const found: Array<[number, string, string]> = [];
  for (const block of document.blocks) {
    const text = snippet(block.text);
    if (text !== "") found.push([block.page, block.type, text]);
  }
  return found;
}

function textSpans(items: ReadonlyArray<readonly [number, string, string]>): string[] {
  if (items.length === 0) return [];
  const pages = [...new Set(items.map((item) => item[0]))].sort((left, right) => left - right);
  const chosen: string[] = [];
  const earlier = pages.length >= 2 ? pages[pages.length - 2] : undefined;
  if (earlier !== undefined) {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item !== undefined && item[0] === earlier) {
        chosen.push(item[2]);
        break;
      }
    }
  }
  for (const item of items) {
    if (item[1] === "footnote" || item[1] === "caption") {
      chosen.push(item[2]);
      break;
    }
  }
  const lastPage = pages[pages.length - 1];
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item !== undefined && item[0] === lastPage) {
      chosen.push(item[2]);
      break;
    }
  }
  const unique: string[] = [];
  for (const text of chosen) if (!unique.includes(text)) unique.push(text);
  return unique;
}

function orderPairs(items: ReadonlyArray<readonly [number, string, string]>): string[][] {
  if (items.length < 2) return [];
  const pages = [...new Set(items.map((item) => item[0]))].sort((left, right) => left - right);
  const pairs: string[][] = [];
  const add = (before: string, after: string): void => {
    if (before === after || pairs.length >= 2) return;
    if (!pairs.some((pair) => pair[0] === before && pair[1] === after)) pairs.push([before, after]);
  };
  const first = items[0];
  const last = items[items.length - 1];
  if (first === undefined || last === undefined) return [];
  if (pages.length >= 2) {
    const left = pages[0];
    const right = pages[1];
    if (left !== undefined && right !== undefined) add(lastOn(items, left), firstOn(items, right));
    if (pages.length > 2) {
      const penultimate = pages[pages.length - 2];
      const finalPage = pages[pages.length - 1];
      if (penultimate !== undefined && finalPage !== undefined) {
        add(lastOn(items, penultimate), firstOn(items, finalPage));
      }
    }
    add(first[2], last[2]);
  } else {
    add(first[2], last[2]);
    const middle = items[Math.floor(items.length / 2)];
    if (middle !== undefined) add(first[2], middle[2]);
  }
  return pairs;
}

function lastOn(items: ReadonlyArray<readonly [number, string, string]>, page: number): string {
  let found = "";
  for (const item of items) if (item[0] === page) found = item[2];
  return found;
}

function firstOn(items: ReadonlyArray<readonly [number, string, string]>, page: number): string {
  for (const item of items) if (item[0] === page) return item[2];
  return "";
}

function fieldDrafts(document: Document): Record<string, unknown> {
  if (document.fields === undefined) return {};
  const fields: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(document.fields)) fields[key] = field.value;
  return fields;
}

function snippet(text: string): string {
  const trimmed = trim(text);
  if (trimmed.length <= SNIPPET) return trimmed;
  let cut = SNIPPET;
  while (cut > 0 && !isWhitespace(trimmed[cut - 1] ?? "")) cut -= 1;
  if (cut === 0) return trimmed.slice(0, SNIPPET);
  return trim(trimmed.slice(0, cut));
}

function trim(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && isWhitespace(text[start] ?? "")) start += 1;
  while (end > start && isWhitespace(text[end - 1] ?? "")) end -= 1;
  return text.slice(start, end);
}

function expectLines(expect: Record<string, unknown>, todo: boolean): string[] {
  const lines: string[] = [];
  const tables = expect.tables;
  if (Array.isArray(tables) && tables.length > 0) {
    lines.push("  tables:");
    for (const table of tables) if (isRecord(table)) lines.push(...tableLines(table, todo));
  }
  const texts = expect.text_contains;
  if (Array.isArray(texts) && texts.length > 0) {
    lines.push("  text_contains:");
    for (const text of texts) if (typeof text === "string" && text !== "") lines.push(`    - ${quote(text)}${mark(todo)}`);
  }
  const pairs = expect.reading_order;
  if (Array.isArray(pairs) && pairs.length > 0) {
    lines.push("  reading_order:");
    for (const pair of pairs) {
      if (Array.isArray(pair) && pair.length === 2) {
        lines.push(`    - - ${quote(String(pair[0]))}${mark(todo)}`);
        lines.push(`      - ${quote(String(pair[1]))}${mark(todo)}`);
      }
    }
  }
  const fields = expect.fields;
  if (isRecord(fields) && Object.keys(fields).length > 0) {
    lines.push("  fields:");
    for (const [key, value] of Object.entries(fields)) {
      lines.push(`    ${fieldKey(key)}: ${scalar(value)}${mark(todo)}`);
    }
  }
  return lines;
}

function tableLines(table: Record<string, unknown>, todo: boolean): string[] {
  const lines = [`    - page: ${scalar(table.page)}${mark(todo)}`];
  const header = table.header;
  if (Array.isArray(header)) {
    lines.push("      header:");
    for (const cell of header) lines.push(`        - ${quote(String(cell))}${mark(todo)}`);
  }
  if ("rows" in table) lines.push(`      rows: ${scalar(table.rows)}${mark(todo)}`);
  if ("cols" in table) lines.push(`      cols: ${scalar(table.cols)}${mark(todo)}`);
  const cells = table.cells;
  if (Array.isArray(cells) && cells.length > 0) {
    lines.push("      cells:");
    for (const cell of cells) {
      if (!isRecord(cell)) continue;
      lines.push(`        - row: ${scalar(cell.row)}${mark(todo)}`);
      lines.push(`          col: ${scalar(cell.col)}${mark(todo)}`);
      lines.push(`          value: ${quote(String(cell.value ?? ""))}${mark(todo)}`);
    }
  }
  return lines;
}

function mark(todo: boolean): string {
  return todo ? TODO : "";
}

function quote(text: string): string {
  return JSON.stringify(text);
}

function scalar(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") {
    if (Number.isFinite(value) && value === Math.trunc(value) && Math.abs(value) <= 2 ** 53) {
      return String(Math.trunc(value));
    }
    return JSON.stringify(value);
  }
  return quote(String(value));
}

function fieldKey(key: string): string {
  return plainKey(key) ? key : quote(key);
}

function plainKey(key: string): boolean {
  if (key === "" || !asciiLetter(key[0] ?? "")) return false;
  return [...key].every((char) => asciiLetter(char) || asciiDigit(char) || char === "_");
}

function asciiLetter(char: string): boolean {
  return (char >= "A" && char <= "Z") || (char >= "a" && char <= "z");
}

function asciiDigit(char: string): boolean {
  return char >= "0" && char <= "9";
}

async function writeDraft(
  source: string,
  parser: string,
  out: string | undefined,
  config: string,
): Promise<string> {
  if (!existsSync(source) || !statFile(source)) {
    throw new LabelError(`${source}: file not found. Pass the source document to label.`);
  }
  const options = parserOptions(config, parser);
  const adapter: Adapter = resolve(parser, options);
  await ensureAvailable([[parser, adapter]]);
  const document = await runParser(adapter, source, parser);
  const target = out ?? path.join("golden", `${path.parse(source).name}.yaml`);
  mkdirSync(path.dirname(target), { recursive: true });
  const fileRef = path.relative(path.dirname(path.resolve(target)), path.resolve(source)).split(path.sep).join("/");
  writeFileSync(target, draftYaml(document, fileRef, path.parse(source).name), "utf8");
  return target;
}

function statFile(filePath: string): boolean {
  try {
    return readFileSync(filePath).length >= 0;
  } catch {
    return false;
  }
}

function parserOptions(config: string, parser: string): Record<string, unknown> {
  if (!existsSync(config)) return {};
  const loaded = loadConfig(config);
  for (const item of loaded.parsers) {
    if (item.id === parser) return { ...(item.options ?? {}) };
  }
  return {};
}

function loadModel(filePath: string): GoldenDraft {
  let loaded: unknown;
  try {
    loaded = parse(readFileSync(filePath, "utf8"), { schema: "core" });
  } catch (err) {
    const message = err instanceof Error ? err.message.split("\n")[0] : String(err);
    throw new LabelError(`${filePath}: invalid YAML: ${message}. Fix the golden file.`);
  }
  if (!isRecord(loaded) || typeof loaded.file !== "string" || loaded.file === "") {
    throw new LabelError(`${filePath}: golden file must be a mapping with file. Fix the golden file.`);
  }
  const id = typeof loaded.id === "string" ? loaded.id : undefined;
  const expect = isRecord(loaded.expect) ? loaded.expect : undefined;
  const model: GoldenDraft = { file: loaded.file };
  if (id !== undefined) model.id = id;
  if (expect !== undefined) model.expect = expect;
  return model;
}

function confirmedYaml(loaded: unknown): string {
  if (!isRecord(loaded) || typeof loaded.file !== "string") {
    throw new LabelError("save body must be a golden file object.");
  }
  const model: GoldenDraft = { file: loaded.file };
  if (typeof loaded.id === "string") model.id = loaded.id;
  if (isRecord(loaded.expect)) model.expect = loaded.expect;
  return renderYaml(model, false);
}

function sourcePath(goldenPath: string, model: GoldenDraft): string {
  return path.resolve(path.dirname(goldenPath), model.file);
}

function send(res: ServerResponse, status: number, contentType: string, body: string): void {
  res.writeHead(status, {
    "Content-Type": contentType,
    "Content-Length": Buffer.byteLength(body),
    Connection: "close",
  });
  res.end(body);
}

function sendSource(res: ServerResponse, source: string): void {
  if (!existsSync(source)) {
    send(res, 404, "text/plain; charset=utf-8", `${source}: source file not found.\n`);
    return;
  }
  const body = readFileSync(source);
  const contentType = source.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream";
  res.writeHead(200, {
    "Content-Type": contentType,
    "Content-Length": body.length,
    Connection: "close",
  });
  res.end(body);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolveBody, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolveBody(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
