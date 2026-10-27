/**
 * Load and validate golden files.
 *
 * @example
 * ```ts
 * import { loadGolden } from "docassay";
 *
 * const files = loadGolden("golden");
 * console.log(files[0]?.id);
 * ```
 */

import { readFileSync, readdirSync, existsSync, openSync, readSync, closeSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020 } from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import {
  parseDocument,
  isMap,
  isScalar,
  isSeq,
  isNode,
  type Node,
  type YAMLError,
  type YAMLMap,
} from "yaml";

import type { CellExpect, ChunkExpect, Expect, GoldenFile, TableExpect } from "./models/golden.js";

declare const __filename: string;

const YAML_SUFFIXES = [".yaml", ".yml"];
const TODO = "# TODO verify";

let validator: ValidateFunction | undefined;

/** One validation problem, located in a golden file. */
export interface GoldenIssue {
  path: string;
  line: number;
  column: number;
  message: string;
}

/**
 * Format a golden-file problem as `path:line:column: message`.
 *
 * @example
 * ```ts
 * formatIssue({ path: "golden/a.yaml", line: 3, column: 1, message: "file not found: doc.pdf" });
 * ```
 */
export function formatIssue(issue: GoldenIssue): string {
  return `${issue.path}:${issue.line}:${issue.column}: ${issue.message}`;
}

/**
 * Raised by {@link loadGolden} when any golden file is invalid.
 *
 * @example
 * ```ts
 * try {
 *   loadGolden("golden");
 * } catch (err) {
 *   if (err instanceof GoldenError) console.log(formatIssue(err.issues[0]!));
 * }
 * ```
 */
export class GoldenError extends Error {
  readonly issues: GoldenIssue[];

  constructor(issues: GoldenIssue[]) {
    const ordered = sortIssues(issues);
    super(ordered.map((issue) => formatIssue(issue)).join("\n"));
    this.name = "GoldenError";
    this.issues = ordered;
  }
}

/** Files, validation issues, and `# TODO verify` marks from one inspection. */
export interface GoldenReport {
  files: GoldenFile[];
  issues: GoldenIssue[];
  todos: GoldenIssue[];
}

/**
 * Load a golden set and return its files sorted by id.
 *
 * `dir` may be a directory of top-level `*.yaml` / `*.yml` files or one golden
 * file. `id` defaults to the file stem. `file` is resolved relative to the
 * golden file and stored relative to the set directory.
 *
 * @example
 * ```ts
 * const files = loadGolden("golden");
 * console.log(files[0]?.id);
 * ```
 */
export function loadGolden(dir: string): GoldenFile[] {
  const report = inspectGolden(dir);
  if (report.issues.length > 0) throw new GoldenError(report.issues);
  return report.files;
}

/**
 * Print golden-set problems and return the `docassay validate` exit code.
 *
 * Exit 0 when the set is valid. Exit 2 when any file is invalid. Exit 1 when
 * `strict` is set and a file still contains `# TODO verify` but nothing else
 * is wrong.
 *
 * @example
 * ```ts
 * process.exitCode = validateCommand("golden", { strict: true });
 * ```
 */
export function validateCommand(dir: string, options: { strict?: boolean } = {}): number {
  const report = inspectGolden(dir);
  if (report.issues.length > 0) {
    for (const issue of report.issues) console.log(formatIssue(issue));
    return 2;
  }
  if (options.strict && report.todos.length > 0) {
    for (const issue of report.todos) console.log(formatIssue(issue));
    return 1;
  }
  return 0;
}

/**
 * Load `dir` without throwing, collecting every issue and TODO.
 *
 * @example
 * ```ts
 * const report = inspectGolden("golden");
 * console.log(report.files.length, report.issues.length);
 * ```
 */
export function inspectGolden(dir: string): GoldenReport {
  const target = path.resolve(dir);
  const shown = displayPath(target);
  if (!existsSync(target)) {
    return { files: [], issues: [issue(shown, 1, 1, "path not found")], todos: [] };
  }
  const stat = statSync(target);
  let paths: string[];
  let setDir: string;
  if (stat.isFile()) {
    if (!isGoldenName(path.basename(target))) {
      return { files: [], issues: [issue(shown, 1, 1, "expected a .yaml or .yml file")], todos: [] };
    }
    paths = [target];
    setDir = path.dirname(target);
  } else if (stat.isDirectory()) {
    let names: string[];
    try {
      names = readdirSync(target)
        .filter((name) => {
          const full = path.join(target, name);
          return statSync(full).isFile() && isGoldenName(name);
        })
        .sort(compareText);
    } catch (err) {
      const message = err instanceof Error && err.message ? err.message : "could not read directory";
      return { files: [], issues: [issue(shown, 1, 1, message)], todos: [] };
    }
    paths = names.map((name) => path.join(target, name));
    setDir = target;
  } else {
    return {
      files: [],
      issues: [issue(shown, 1, 1, "path is not a golden file or directory")],
      todos: [],
    };
  }

  const issues: GoldenIssue[] = [];
  const todos: GoldenIssue[] = [];
  const files: GoldenFile[] = [];
  const seenIds = new Map<string, string>();
  for (const goldenPath of paths) {
    const loaded = loadOne(goldenPath, setDir);
    issues.push(...loaded.issues);
    todos.push(...loaded.todos);
    if (loaded.goldenId !== null) {
      const previous = seenIds.get(loaded.goldenId);
      const fileShown = displayPath(goldenPath);
      if (previous !== undefined) {
        issues.push(
          issue(
            fileShown,
            loaded.idLine,
            loaded.idColumn,
            `duplicate id "${loaded.goldenId}" (also in ${previous})`,
          ),
        );
      } else {
        seenIds.set(loaded.goldenId, fileShown);
      }
    }
    if (loaded.model !== null && loaded.issues.length === 0) files.push(loaded.model);
  }
  files.sort((a, b) => compareText(a.id ?? "", b.id ?? ""));
  return { files, issues: sortIssues(issues), todos: sortIssues(todos) };
}

/**
 * Serialize golden files to the JSON both language ports must match.
 *
 * Integer-valued numbers are written without a decimal point. Missing optional
 * fields are omitted; explicit null field values are kept.
 *
 * @example
 * ```ts
 * const text = canonicalGoldenJson(loadGolden("golden"));
 * ```
 */
export function canonicalGoldenJson(files: GoldenFile[]): string {
  return `${JSON.stringify(files.map(orderGolden), null, 2)}\n`;
}

interface Loaded {
  model: GoldenFile | null;
  goldenId: string | null;
  idLine: number;
  idColumn: number;
  issues: GoldenIssue[];
  todos: GoldenIssue[];
}

function loadOne(goldenPath: string, setDir: string): Loaded {
  const shown = displayPath(goldenPath);
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(goldenPath));
  } catch (err) {
    const message = err instanceof TypeError ? "file is not valid UTF-8" : "could not read file";
    return empty([issue(shown, 1, 1, message)], []);
  }
  const todos = todoIssues(text, shown);
  const doc = parseDocument(text, { schema: "core", uniqueKeys: false });
  if (doc.errors.length > 0) {
    return empty(
      doc.errors.map((err) => yamlIssue(shown, err)),
      todos,
    );
  }
  const root = doc.contents;
  const data: unknown = doc.toJS();
  if (!isMap(root) || !isRecord(data)) {
    const pos = positionOf(text, root);
    return empty([issue(shown, pos.line, pos.column, "golden file must be a mapping")], todos);
  }

  const issues = duplicateKeys(root, text, shown);
  const originalFile = typeof data.file === "string" ? data.file : "";
  applyIdentity(data, goldenPath, setDir, originalFile);
  const idPos = idPosition(text, root);
  const goldenId = typeof data.id === "string" && data.id.length > 0 ? data.id : null;
  issues.push(...schemaIssues(data, text, root, shown));
  issues.push(...extraIssues(data, text, root, goldenPath, originalFile, shown));
  let model: GoldenFile | null = null;
  if (!issues.some((item) => isSchemaMessage(item.message))) {
    model = toGolden(data);
  }
  return { model, goldenId, idLine: idPos.line, idColumn: idPos.column, issues, todos };
}

function empty(issues: GoldenIssue[], todos: GoldenIssue[]): Loaded {
  return { model: null, goldenId: null, idLine: 1, idColumn: 1, issues, todos };
}

function applyIdentity(
  data: Record<string, unknown>,
  goldenPath: string,
  setDir: string,
  originalFile: string,
): void {
  if (!Object.prototype.hasOwnProperty.call(data, "id")) data.id = path.parse(goldenPath).name;
  if (originalFile) data.file = storedPath(originalFile, goldenPath, setDir);
}

function storedPath(originalFile: string, goldenPath: string, setDir: string): string {
  const absolute = path.resolve(path.dirname(goldenPath), originalFile);
  return path.relative(path.resolve(setDir), absolute).split(path.sep).join("/");
}

function extraIssues(
  data: Record<string, unknown>,
  text: string,
  root: YAMLMap,
  goldenPath: string,
  originalFile: string,
  shown: string,
): GoldenIssue[] {
  const issues: GoldenIssue[] = [];
  if (originalFile) {
    const target = path.resolve(path.dirname(goldenPath), originalFile);
    const pos = positionOf(text, nodeAt(root, ["file"], null));
    if (!existsSync(target)) {
      issues.push(issue(shown, pos.line, pos.column, `file not found: ${originalFile}`));
    } else if (!isReadableFile(target)) {
      issues.push(issue(shown, pos.line, pos.column, `file is not readable: ${originalFile}`));
    }
  }
  if (isRecord(data.expect)) {
    issues.push(...tableIssues(data.expect, text, root, shown));
    issues.push(...orderIssues(data.expect, text, root, shown));
  }
  return issues;
}

function isReadableFile(target: string): boolean {
  try {
    if (!statSync(target).isFile()) return false;
    const handle = openSync(target, "r");
    try {
      const buffer = Buffer.alloc(1);
      readSync(handle, buffer, 0, 1, 0);
    } finally {
      closeSync(handle);
    }
  } catch {
    return false;
  }
  return true;
}

function tableIssues(
  expect: Record<string, unknown>,
  text: string,
  root: Node,
  shown: string,
): GoldenIssue[] {
  const issues: GoldenIssue[] = [];
  if (!Array.isArray(expect.tables)) return issues;
  expect.tables.forEach((table, index) => {
    if (!isRecord(table) || !Array.isArray(table.cells)) return;
    const header = Array.isArray(table.header) ? table.header : null;
    table.cells.forEach((cell, cellIndex) => {
      if (!isRecord(cell)) return;
      const col = cell.col;
      const pos = positionOf(text, nodeAt(root, ["expect", "tables", String(index), "cells", String(cellIndex), "col"], null));
      if (typeof col === "string") {
        if (header === null || !header.includes(col)) {
          issues.push(issue(shown, pos.line, pos.column, `column "${col}" is not in the header`));
        }
      } else if (typeof col === "number" && Number.isInteger(col) && header !== null && col >= header.length) {
        issues.push(issue(shown, pos.line, pos.column, `column index ${col} is outside the header`));
      }
    });
  });
  return issues;
}

function orderIssues(
  expect: Record<string, unknown>,
  text: string,
  root: Node,
  shown: string,
): GoldenIssue[] {
  const issues: GoldenIssue[] = [];
  if (!Array.isArray(expect.reading_order)) return issues;
  expect.reading_order.forEach((pair, index) => {
    if (
      Array.isArray(pair) &&
      pair.length === 2 &&
      typeof pair[0] === "string" &&
      typeof pair[1] === "string" &&
      pair[0] !== "" &&
      pair[1] !== "" &&
      pair[0] === pair[1]
    ) {
      const pos = positionOf(text, nodeAt(root, ["expect", "reading_order", String(index)], null));
      issues.push(issue(shown, pos.line, pos.column, "reading_order pair must contain two different strings"));
    }
  });
  return issues;
}

function schemaIssues(data: Record<string, unknown>, text: string, root: Node, shown: string): GoldenIssue[] {
  const validate = schemaValidator();
  validate(data);
  const errors = validate.errors ?? [];
  const oneOfPaths = new Set(errors.filter((error) => error.keyword === "oneOf").map((error) => error.instancePath));
  const issues: GoldenIssue[] = [];
  for (const error of errors) {
    if (oneOfPaths.has(error.instancePath) && error.keyword !== "oneOf") continue;
    if (error.keyword === "additionalProperties") {
      const extra = String(param(error, "additionalProperty") ?? "");
      const pos = positionOf(text, nodeAt(root, pointer(error.instancePath), extra));
      issues.push(issue(shown, pos.line, pos.column, `unknown property "${extra}"`));
      continue;
    }
    const pos = positionOf(text, nodeAt(root, pointer(error.instancePath), null));
    issues.push(issue(shown, pos.line, pos.column, schemaMessage(error)));
  }
  return issues;
}

function schemaMessage(error: ErrorObject): string {
  if (error.keyword === "required") return `missing property "${String(param(error, "missingProperty") ?? "")}"`;
  if (error.keyword === "type") return typeMessage(param(error, "type"));
  if (error.keyword === "minLength") {
    const limit = param(error, "limit");
    return limit === 1 ? "must not be empty" : `must be at least ${String(limit)} characters`;
  }
  if (error.keyword === "minimum") return `must be >= ${String(param(error, "limit"))}`;
  if (error.keyword === "minItems") return `must have at least ${String(param(error, "limit"))} items`;
  if (error.keyword === "maxItems") return `must have at most ${String(param(error, "limit"))} items`;
  if (error.keyword === "oneOf") {
    return error.instancePath.endsWith("/col")
      ? "must be a column index or a header name"
      : "must match one schema";
  }
  return `schema violation (${error.keyword})`;
}

function typeMessage(expected: unknown): string {
  if (Array.isArray(expected)) {
    const names = expected.map((item) => String(item));
    if (names.length === 1) return `must be ${names[0] ?? ""}`;
    const last = names[names.length - 1] ?? "";
    return `must be ${names.slice(0, -1).join(", ")}, or ${last}`;
  }
  return `must be ${String(expected)}`;
}

function isSchemaMessage(message: string): boolean {
  return (
    message.startsWith("unknown property ") ||
    message.startsWith("missing property ") ||
    message.startsWith("must be ") ||
    message.startsWith("must not be empty") ||
    message.startsWith("must have at least ") ||
    message.startsWith("must have at most ") ||
    message.startsWith("schema violation ")
  );
}

function pointer(instancePath: string): string[] {
  if (!instancePath) return [];
  return instancePath
    .split("/")
    .slice(1)
    .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function asNode(value: unknown): Node | null {
  return isNode(value) ? value : null;
}

function nodeAt(node: Node | null, parts: string[], key: string | null): Node | null {
  let current: Node | null = node;
  for (const part of parts) {
    if (current === null) return null;
    if (isSeq(current)) {
      const next = asNode(current.items[Number(part)]);
      if (next === null) return current;
      current = next;
      continue;
    }
    if (isMap(current)) {
      const pair = current.items.find((item) => isScalar(item.key) && String(item.key.value) === part);
      const value = asNode(pair?.value);
      if (value === null) return current;
      current = value;
      continue;
    }
    return current;
  }
  if (key !== null && current !== null && isMap(current)) {
    const pair = current.items.find((item) => isScalar(item.key) && String(item.key.value) === key);
    const keyNode = asNode(pair?.key);
    if (keyNode !== null) return keyNode;
  }
  return current;
}

function duplicateKeys(node: Node, text: string, shown: string): GoldenIssue[] {
  const issues: GoldenIssue[] = [];
  if (isMap(node)) {
    const seen = new Set<string>();
    for (const item of node.items) {
      if (isScalar(item.key)) {
        const key = String(item.key.value);
        if (seen.has(key)) {
          const pos = positionOf(text, item.key);
          issues.push(issue(shown, pos.line, pos.column, `duplicate key "${key}"`));
        } else {
          seen.add(key);
        }
      }
      const child = asNode(item.value);
      if (child !== null) issues.push(...duplicateKeys(child, text, shown));
    }
  } else if (isSeq(node)) {
    for (const item of node.items) {
      const child = asNode(item);
      if (child !== null) issues.push(...duplicateKeys(child, text, shown));
    }
  }
  return issues;
}

function idPosition(text: string, node: YAMLMap): { line: number; column: number } {
  const pair = node.items.find((item) => isScalar(item.key) && item.key.value === "id");
  const value = asNode(pair?.value);
  if (value !== null) return positionOf(text, value);
  return positionOf(text, node);
}

function positionOf(text: string, node: Node | null): { line: number; column: number } {
  const offset = node?.range?.[0];
  return positionAt(text, offset);
}

function positionAt(text: string, offset: number | null | undefined): { line: number; column: number } {
  if (offset === null || offset === undefined || offset < 0) return { line: 1, column: 1 };
  let line = 1;
  let column = 1;
  const end = Math.min(offset, text.length);
  for (let index = 0; index < end; index += 1) {
    if (text.charCodeAt(index) === 10) {
      line += 1;
      column = 1;
    } else {
      column += 1;
    }
  }
  return { line, column };
}

function yamlIssue(shown: string, err: YAMLError): GoldenIssue {
  const pos = err.linePos?.[0];
  const line = pos?.line ?? 1;
  const column = pos?.col ?? 1;
  const detail = err.message.split("\n", 1)[0] ?? err.message;
  return issue(shown, line, column, `invalid YAML: ${detail}`);
}

function todoIssues(text: string, shown: string): GoldenIssue[] {
  const issues: GoldenIssue[] = [];
  const lines = text.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index] ?? "";
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    const column = line.indexOf(TODO);
    if (column >= 0) issues.push(issue(shown, index + 1, column + 1, "unresolved TODO verify"));
  }
  return issues;
}

function toGolden(data: Record<string, unknown>): GoldenFile {
  if (typeof data.file !== "string") {
    throw new GoldenError([issue("golden", 1, 1, "golden file is missing file")]);
  }
  const golden: GoldenFile = { file: data.file };
  if (typeof data.id === "string") golden.id = data.id;
  if (Array.isArray(data.tags)) golden.tags = data.tags.map((tag) => String(tag));
  if (isRecord(data.expect)) golden.expect = orderExpect(data.expect) as Expect;
  return golden;
}

function orderGolden(file: GoldenFile): Record<string, unknown> {
  const out: Record<string, unknown> = { file: file.file };
  if (file.id !== undefined) out.id = file.id;
  if (file.tags !== undefined) out.tags = file.tags;
  if (file.expect !== undefined) out.expect = orderExpect(file.expect);
  return out;
}

function orderExpect(expect: Expect | Record<string, unknown>): Record<string, unknown> {
  const source = expect as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  if (Array.isArray(source.tables)) out.tables = source.tables.map((table) => orderTable(table as TableExpect));
  if (source.text_contains !== undefined) out.text_contains = source.text_contains;
  if (source.reading_order !== undefined) out.reading_order = source.reading_order;
  if (isRecord(source.fields)) out.fields = source.fields;
  if (isRecord(source.chunks)) out.chunks = orderChunk(source.chunks as ChunkExpect);
  return out;
}

function orderTable(table: TableExpect): Record<string, unknown> {
  const out: Record<string, unknown> = { page: table.page };
  if (table.header !== undefined) out.header = table.header;
  if (table.rows !== undefined) out.rows = table.rows;
  if (table.cols !== undefined) out.cols = table.cols;
  if (table.cells !== undefined) out.cells = table.cells.map(orderCell);
  return out;
}

function orderCell(cell: CellExpect): Record<string, unknown> {
  return { row: cell.row, col: cell.col, value: cell.value };
}

function orderChunk(chunk: ChunkExpect): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (chunk.max_tokens !== undefined) out.max_tokens = chunk.max_tokens;
  if (chunk.min_tokens !== undefined) out.min_tokens = chunk.min_tokens;
  if (chunk.no_split_tables !== undefined) out.no_split_tables = chunk.no_split_tables;
  if (chunk.heading_paths !== undefined) {
    out.heading_paths = chunk.heading_paths.map((item) => ({ text: item.text, path: item.path }));
  }
  return out;
}

function schemaValidator(): ValidateFunction {
  if (validator) return validator;
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  const compiled = ajv.compile(JSON.parse(readFileSync(findSchema(), "utf8")) as object);
  validator = compiled;
  return compiled;
}

function moduleDirectory(): string {
  if (import.meta.url) return path.dirname(fileURLToPath(import.meta.url));
  return path.dirname(__filename);
}

function findSchema(): string {
  let dir = moduleDirectory();
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = path.join(dir, "spec", "golden.schema.json");
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new GoldenError([
    issue(
      "spec/golden.schema.json",
      1,
      1,
      "could not find spec/golden.schema.json; run from a docassay checkout",
    ),
  ]);
}

function displayPath(file: string): string {
  return path.relative(process.cwd(), path.resolve(file)).split(path.sep).join("/");
}

function isGoldenName(name: string): boolean {
  const lower = name.toLowerCase();
  return YAML_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function param(error: ErrorObject, key: string): unknown {
  if (typeof error.params !== "object" || error.params === null) return undefined;
  return (error.params as Record<string, unknown>)[key];
}

function issue(file: string, line: number, column: number, message: string): GoldenIssue {
  return { path: file, line, column, message };
}

function sortIssues(issues: GoldenIssue[]): GoldenIssue[] {
  return [...issues].sort((a, b) => {
    return (
      compareText(a.path, b.path) ||
      a.line - b.line ||
      a.column - b.column ||
      compareText(a.message, b.message)
    );
  });
}

function compareText(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}
