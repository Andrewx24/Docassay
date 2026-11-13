/**
 * Check extracted values against the spans they cite.
 *
 * A field is verified when any citation's span contains the value. Numbers
 * match `parseNumber` tokens, dates match `parseDate` substrings, booleans
 * are trusted once the span exists, and strings use `normText`. Malformed
 * input returns `citation_invalid` instead of throwing.
 *
 * @example
 * ```ts
 * import { verify } from "docassay/verify";
 *
 * verify(doc, { total: { value: 4120, citations: [{ page: 1, blockId: "b1" }] } });
 * ```
 */

import { parseDate } from "./normalize/dates.js";
import { parseNumber } from "./normalize/numbers.js";
import { normText } from "./normalize/text.js";

export type VerifyStatus = "verified" | "value_not_found" | "citation_invalid" | "uncited";

/** A page, and optionally a block id or a box, pointing at the source span. */
export interface Citation {
  page: number;
  blockId?: string;
  bbox?: number[];
}

/** One field after `verify`. */
export interface FieldCheck {
  name: string;
  status: VerifyStatus;
  weak?: boolean;
  citation?: Citation;
  reason?: string;
}

/** Checks for every field. `ok` is true when each one is verified. */
export interface VerifyResult {
  ok: boolean;
  checks: FieldCheck[];
  unverified: FieldCheck[];
}

const UNCITED = "field has no citations. Add a citation with a page and a blockId, bbox, or page.";
const NOT_FOUND = "value not found in span";
const NULL_VALUE = "value is null. Pass a string, number, or boolean.";
const BAD_VALUE = "value must be a string, number, boolean, or null.";
const BAD_FIELDS = "fields must be an object of field names.";
const BAD_FIELD = "field must be an object with a value.";
const BAD_CITATIONS = "citations must be a list.";
const BAD_DOC = "document must be an object with pages, blocks, and tables.";
const NEED_PAGE = "citation needs a page number. Set page to an integer >= 1.";
const BAD_BBOX = "bbox must be four numbers [x0, y0, x1, y1].";
const BAD_BLOCK_ID = "blockId must be a non-empty string.";
const BAD_READ = "document or fields could not be read. Pass a document object and a map of fields.";

const FULL_MONTHS =
  "january|february|march|april|june|july|august|september|october|november|december";
const ABBR_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec";
const MONTH = `(?:${FULL_MONTHS}|(?:${ABBR_MONTHS})\\.?)`;
const DATE_RE = new RegExp(
  `${MONTH} [0-9]{1,2},? [0-9]{4}|[0-9]{1,2} ${MONTH} [0-9]{4}|[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}|[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{4}`,
  "g",
);
const NUMBER_RE = /[(-]?[$€£]?[0-9][0-9.,' ]*[0-9]?%?\)?/g;

interface Piece {
  kind: "block" | "table";
  id: string;
  page: number;
  text: string;
  bbox: Box | null;
}

type Box = [number, number, number, number];

interface Index {
  blocks: Piece[];
  tables: Piece[];
  byId: Map<string, Piece>;
  pages: Set<number>;
  pageText: Map<number, string>;
}

interface View {
  kind: "bool" | "number" | "date" | "string" | "null" | "bad";
  number: number | null;
  date: string | null;
  text: string | null;
}

interface Hit {
  status: VerifyStatus;
  citation: Citation | null;
  reason: string | null;
  pageOnly: boolean;
}

/**
 * Check each field against the document span its citations name.
 *
 * @example
 * ```ts
 * verify(doc, { total: { value: 4120, citations: [{ page: 1, blockId: "b1" }] } });
 * ```
 */
export function verify(doc: unknown, fields: unknown): VerifyResult {
  try {
    return run(doc, fields);
  } catch {
    return fail("", "citation_invalid", BAD_READ);
  }
}

function run(doc: unknown, fields: unknown): VerifyResult {
  if (!isDocument(doc)) return fail("", "citation_invalid", BAD_DOC);
  const items = fieldItems(fields);
  if (items === null) return fail("", "citation_invalid", BAD_FIELDS);
  const index = buildIndex(doc);
  const checks = items.map(([name, raw]) => checkField(index, name, raw));
  const unverified = checks.filter((check) => check.status !== "verified");
  return { ok: unverified.length === 0, checks, unverified };
}

function fail(name: string, status: VerifyStatus, reason: string): VerifyResult {
  const check: FieldCheck = { name, status, reason };
  return { ok: false, checks: [check], unverified: [check] };
}

function checkField(index: Index, name: string, raw: unknown): FieldCheck {
  if (!isRecord(raw) || !("value" in raw)) {
    return { name, status: "citation_invalid", reason: BAD_FIELD };
  }
  const view = classify(raw.value);
  if (!("citations" in raw) || raw.citations == null || (Array.isArray(raw.citations) && raw.citations.length === 0)) {
    return { name, status: "uncited", reason: UNCITED };
  }
  if (!Array.isArray(raw.citations)) {
    return { name, status: "citation_invalid", reason: BAD_CITATIONS };
  }
  let first: Hit | null = null;
  let searched = false;
  for (const citation of raw.citations) {
    const hit = oneCitation(index, view, citation);
    if (first === null) first = hit;
    if (hit.status === "verified") return fromHit(name, hit, view, true);
    if (hit.status === "value_not_found") searched = true;
  }
  if (first === null) return { name, status: "uncited", reason: UNCITED };
  if (searched) return fromHit(name, first, view, false);
  return checkFrom(name, "citation_invalid", first.citation, first.reason, false);
}

function fromHit(name: string, hit: Hit, view: View, passed: boolean): FieldCheck {
  const status: VerifyStatus = passed ? "verified" : "value_not_found";
  const weak = (view.kind === "bool" && passed) || hit.pageOnly;
  const reason = passed ? null : view.kind === "null" ? NULL_VALUE : NOT_FOUND;
  return checkFrom(name, status, hit.citation, reason, weak);
}

function checkFrom(
  name: string,
  status: VerifyStatus,
  citation: Citation | null,
  reason: string | null,
  weak: boolean,
): FieldCheck {
  const check: FieldCheck = { name, status };
  if (weak) check.weak = true;
  if (citation !== null) check.citation = citation;
  if (reason !== null) check.reason = reason;
  return check;
}

function oneCitation(index: Index, view: View, citation: unknown): Hit {
  if (!isRecord(citation)) return { status: "citation_invalid", citation: null, reason: NEED_PAGE, pageOnly: false };
  const page = asPage(citation.page);
  if (page === null) {
    return { status: "citation_invalid", citation: null, reason: NEED_PAGE, pageOnly: false };
  }
  const blockId = readBlockId(citation);
  if (blockId === false) {
    return { status: "citation_invalid", citation: echo(page, null, null), reason: BAD_BLOCK_ID, pageOnly: false };
  }
  if (typeof blockId === "string") return byId(index, view, page, blockId);
  const bbox = readBBox(citation);
  if (bbox === false) {
    return { status: "citation_invalid", citation: echo(page, null, null), reason: BAD_BBOX, pageOnly: false };
  }
  if (bbox !== null) return byBBox(index, view, page, bbox);
  if (!index.pages.has(page)) {
    return { status: "citation_invalid", citation: echo(page, null, null), reason: missingPage(page), pageOnly: true };
  }
  return search(view, pageText(index, page), echo(page, null, null), true);
}

function byId(index: Index, view: View, page: number, blockId: string): Hit {
  const citation = echo(page, blockId, null);
  const piece = index.byId.get(blockId);
  if (piece === undefined) {
    return { status: "citation_invalid", citation, reason: missingId(blockId), pageOnly: false };
  }
  if (piece.page !== page) {
    return { status: "citation_invalid", citation, reason: wrongPage(piece.kind, blockId, piece.page, page), pageOnly: false };
  }
  return search(view, piece.text, citation, false);
}

function byBBox(index: Index, view: View, page: number, bbox: Box): Hit {
  const citation = echo(page, null, bbox);
  if (!index.pages.has(page)) {
    return { status: "citation_invalid", citation, reason: missingPage(page), pageOnly: false };
  }
  const parts: string[] = [];
  for (const piece of index.blocks) {
    if (piece.page === page && piece.bbox !== null && overlaps(piece.bbox, bbox)) parts.push(piece.text);
  }
  for (const piece of index.tables) {
    if (piece.page === page && piece.bbox !== null && overlaps(piece.bbox, bbox)) parts.push(piece.text);
  }
  return search(view, parts.join(" "), citation, false);
}

function search(view: View, span: string, citation: Citation, pageOnly: boolean): Hit {
  if (view.kind === "bad") return { status: "citation_invalid", citation, reason: BAD_VALUE, pageOnly };
  if (view.kind === "null") return { status: "value_not_found", citation, reason: NULL_VALUE, pageOnly };
  if (view.kind === "bool" || inSpan(view, span)) {
    return { status: "verified", citation, reason: null, pageOnly };
  }
  return { status: "value_not_found", citation, reason: NOT_FOUND, pageOnly };
}

function inSpan(view: View, span: string): boolean {
  if (view.kind === "number" && view.number !== null) return spanHasNumber(span, view.number);
  if (view.kind === "date" && view.date !== null) return spanHasDate(span, view.date);
  if (view.kind === "string" && view.text !== null) {
    return normText(span).includes(normText(view.text));
  }
  return false;
}

function spanHasNumber(span: string, expected: number): boolean {
  if (!Number.isFinite(expected)) return false;
  NUMBER_RE.lastIndex = 0;
  const matches = span.match(NUMBER_RE) ?? [];
  for (const token of matches) {
    const parsed = parseNumber(token);
    if (parsed !== null && Math.abs(expected - parsed) <= 1e-6 * Math.max(1, Math.abs(parsed))) {
      return true;
    }
  }
  return false;
}

function spanHasDate(span: string, target: string): boolean {
  DATE_RE.lastIndex = 0;
  const matches = normText(span).match(DATE_RE) ?? [];
  for (const token of matches) {
    if (parseDate(token) === target) return true;
  }
  return false;
}

function classify(value: unknown): View {
  if (typeof value === "boolean") return { kind: "bool", number: null, date: null, text: null };
  if (typeof value === "number") return { kind: "number", number: value, date: null, text: null };
  if (typeof value === "string") {
    const dated = parseDate(value);
    if (dated !== null) return { kind: "date", number: null, date: dated, text: null };
    const number = parseNumber(value);
    if (number !== null) return { kind: "number", number, date: null, text: null };
    return { kind: "string", number: null, date: null, text: value };
  }
  if (value === null) return { kind: "null", number: null, date: null, text: null };
  return { kind: "bad", number: null, date: null, text: null };
}

function buildIndex(doc: Record<string, unknown>): Index {
  const blocks = readPieces("block", doc.blocks);
  const tables = readPieces("table", doc.tables);
  const byId = new Map<string, Piece>();
  for (const piece of [...blocks, ...tables]) {
    if (!byId.has(piece.id)) byId.set(piece.id, piece);
  }
  const explicit = explicitPages(doc.pages);
  const pages = explicit ?? new Set([...blocks, ...tables].map((piece) => piece.page));
  return { blocks, tables, byId, pages, pageText: new Map() };
}

function pageText(index: Index, page: number): string {
  const cached = index.pageText.get(page);
  if (cached !== undefined) return cached;
  const parts = [
    ...index.blocks.filter((piece) => piece.page === page).map((piece) => piece.text),
    ...index.tables.filter((piece) => piece.page === page).map((piece) => piece.text),
  ];
  const text = parts.join(" ");
  index.pageText.set(page, text);
  return text;
}

function readPieces(kind: "block" | "table", value: unknown): Piece[] {
  if (!Array.isArray(value)) return [];
  const found: Piece[] = [];
  for (const item of value) {
    const parsed = readPiece(kind, item);
    if (parsed !== null) found.push(parsed);
  }
  return found;
}

function readPiece(kind: "block" | "table", item: unknown): Piece | null {
  if (!isRecord(item)) return null;
  const id = item.id;
  const page = asPage(item.page);
  if (typeof id !== "string" || id === "" || page === null) return null;
  const text = kind === "table" ? tableText(item) : typeof item.text === "string" ? item.text : "";
  return { kind, id, page, text, bbox: box(item.bbox) };
}

function tableText(table: Record<string, unknown>): string {
  const cells: string[] = [];
  if (Array.isArray(table.header)) {
    for (const cell of table.header) cells.push(typeof cell === "string" ? cell : "");
  }
  if (Array.isArray(table.rows)) {
    for (const row of table.rows) {
      if (!Array.isArray(row)) continue;
      for (const cell of row) cells.push(typeof cell === "string" ? cell : "");
    }
  }
  return cells.join(" ");
}

function explicitPages(value: unknown): Set<number> | null {
  if (value === undefined) return null;
  const found = new Set<number>();
  if (!Array.isArray(value)) return found;
  for (const item of value) {
    const page = isRecord(item) ? asPage(item.number) : asPage(item);
    if (page !== null) found.add(page);
  }
  return found;
}

function readBlockId(citation: Record<string, unknown>): string | null | false {
  const raw = "blockId" in citation ? citation.blockId : "block_id" in citation ? citation.block_id : undefined;
  if (raw === undefined || raw === null) return null;
  if (typeof raw === "string" && raw !== "") return raw;
  return false;
}

function readBBox(citation: Record<string, unknown>): Box | null | false {
  if (!("bbox" in citation) || citation.bbox == null) return null;
  const parsed = box(citation.bbox);
  return parsed === null ? false : parsed;
}

function box(value: unknown): Box | null {
  const raw = isRecord(value) && "root" in value ? value.root : value;
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const nums: number[] = [];
  for (const item of raw) {
    if (typeof item !== "number" || !Number.isFinite(item)) return null;
    nums.push(item);
  }
  const [x0, y0, x1, y1] = nums;
  if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) return null;
  return [x0, y0, x1, y1];
}

function overlaps(left: Box, right: Box): boolean {
  return Math.min(left[2], right[2]) > Math.max(left[0], right[0]) &&
    Math.min(left[3], right[3]) > Math.max(left[1], right[1]);
}

function echo(page: number, blockId: string | null, bbox: Box | null): Citation {
  const citation: Citation = { page };
  if (blockId !== null) citation.blockId = blockId;
  if (bbox !== null) citation.bbox = bbox.map((number) => jsonNumber(number));
  return citation;
}

function jsonNumber(number: number): number {
  if (Number.isInteger(number) && Math.abs(number) <= 2 ** 53) return number;
  return number;
}

function asPage(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) return null;
  return value;
}

function missingPage(page: number): string {
  return `page ${page} does not exist. Cite a page listed on the document.`;
}

function missingId(blockId: string): string {
  return `no block or table with id ${blockId}. Cite an id from the document.`;
}

function wrongPage(kind: string, id: string, actual: number, cited: number): string {
  return `${kind} ${id} is on page ${actual}, citation says page ${cited}. Use the ${kind}'s page.`;
}

function isDocument(doc: unknown): doc is Record<string, unknown> {
  return isRecord(doc);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fieldItems(fields: unknown): [string, unknown][] | null {
  if (!isRecord(fields)) return null;
  return Object.entries(fields);
}
