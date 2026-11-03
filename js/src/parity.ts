#!/usr/bin/env node
/**
 * Score one shared fixture so Python and TypeScript can be diffed.
 *
 * The JSON is `scores` (including `overall`), `failures`, and `extra` when
 * the chunk scorer reports token stats. Whole numbers are written as
 * integers so `1.0` and `1` compare equal.
 *
 * @example
 * ```sh
 * node js/dist/parity.js tables-exact
 * ```
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "yaml";

import type { Document } from "./models/document.js";
import type { GoldenFile } from "./models/golden.js";
import { documentScores } from "./scoring/aggregate.js";
import {
  scoreChunks,
  scoreFields,
  scoreOrder,
  scoreTables,
  scoreText,
  type Failure,
  type ScoreOptions,
} from "./scoring/index.js";
import type { FieldValue } from "./scoring/fields.js";

/** Scores, failures, and optional chunk token stats for one fixture. */
export interface ParityResult {
  scores: Record<string, number>;
  failures: Failure[];
  extra?: Record<string, unknown>;
}

/** A fixture that cannot be scored. */
export class ParityError extends Error {
  /**
   * @example
   * ```ts
   * new ParityError("unknown fixture 'missing'. Pass spec/fixtures/<case>.");
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "ParityError";
  }
}

/**
 * Score `folder` and return the shared parity object.
 *
 * `folder` contains `document.json`, `golden.yaml`, and an optional
 * `options.json`. Scores use the same functions as `docassay run`.
 *
 * @example
 * ```ts
 * scoreCase("spec/fixtures/tables-exact").scores.tables;
 * ```
 */
export function scoreCase(folder: string): ParityResult {
  const document = loadDocument(path.join(folder, "document.json"));
  const golden = loadGolden(path.join(folder, "golden.yaml"));
  const options = loadOptions(path.join(folder, "options.json"));
  const raw: Record<string, number> = {};
  const failures: Failure[] = [];
  let extra: Record<string, unknown> | undefined;
  const expect = golden.expect;
  if (expect !== undefined) {
    if (expect.tables !== undefined) {
      const result = scoreTables(document, expect.tables, options);
      raw.tables = result.score;
      failures.push(...result.failures);
    }
    if (expect.text_contains !== undefined) {
      const result = scoreText(document, expect.text_contains, options);
      raw.text = result.score;
      failures.push(...result.failures);
    }
    if (expect.reading_order !== undefined) {
      const result = scoreOrder(document, orderPairs(expect.reading_order), options);
      raw.order = result.score;
      failures.push(...result.failures);
    }
    if (expect.fields !== undefined) {
      const result = scoreFields(document, fieldMap(expect.fields), options);
      raw.fields = result.score;
      failures.push(...result.failures);
    }
    if (expect.chunks !== undefined) {
      const result = scoreChunks(document, expect.chunks, options);
      raw.chunks = result.score;
      failures.push(...result.failures);
      if (result.extra !== undefined) extra = result.extra;
    }
  }
  const payload: ParityResult = { scores: documentScores(raw), failures: readyFailures(failures) };
  if (extra !== undefined) payload.extra = readyRecord(extra);
  return payload;
}

/**
 * Return `payload` as indented JSON with a trailing newline.
 *
 * @example
 * ```ts
 * render({ scores: { tables: 1 }, failures: [] });
 * ```
 */
export function render(payload: ParityResult): string {
  return `${JSON.stringify(readyValue(payload), null, 2)}\n`;
}

/**
 * Return the fixture directory named by `argument`.
 *
 * A directory that already contains `document.json` is used as-is.
 * Otherwise `argument` is a folder name under `spec/fixtures/`.
 *
 * @example
 * ```ts
 * resolveCase("tables-exact").endsWith("tables-exact");
 * ```
 */
export function resolveCase(argument: string): string {
  if (existsSync(path.join(argument, "document.json"))) return path.resolve(argument);
  const candidate = path.join(repoRoot(), "spec", "fixtures", argument);
  if (existsSync(path.join(candidate, "document.json"))) return candidate;
  throw new ParityError(
    `unknown fixture ${JSON.stringify(argument)}. ` +
      "Pass a spec/fixtures/<case> directory that contains document.json.",
  );
}

/**
 * Print one case as JSON. Returns 0 on success and 1 on a fixture error.
 *
 * @example
 * ```ts
 * main(["tables-exact"]);
 * ```
 */
export function main(argv: readonly string[]): number {
  try {
    if (argv.length !== 1) {
      throw new ParityError(
        "usage: node js/dist/parity.js <case>\nexample: node js/dist/parity.js tables-exact",
      );
    }
    const folder = resolveCase(argv[0] ?? "");
    process.stdout.write(render(scoreCase(folder)));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    process.stderr.write(`${message}\n`);
    return 1;
  }
  return 0;
}

function repoRoot(): string {
  let current = path.dirname(fileURLToPath(import.meta.url));
  for (let depth = 0; depth < 6; depth += 1) {
    if (
      existsSync(path.join(current, "spec", "fixtures")) &&
      existsSync(path.join(current, "js"))
    ) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new ParityError(
    "could not find the repo root from the parity module. Run it from a docassay checkout.",
  );
}

function loadDocument(filePath: string): Document {
  const loaded = readJson(filePath, "Fix document.json.");
  if (!isRecord(loaded)) {
    throw new ParityError(`${filePath}: document must be a JSON object. Fix document.json.`);
  }
  return loaded as unknown as Document;
}

function loadGolden(filePath: string): GoldenFile {
  let loaded: unknown;
  try {
    loaded = parse(readFileSync(filePath, "utf8"), { schema: "core" });
  } catch (err) {
    const message = err instanceof Error ? err.message.split("\n")[0] : String(err);
    throw new ParityError(`${filePath}: invalid YAML: ${message}. Fix golden.yaml.`);
  }
  if (!isRecord(loaded) || typeof loaded.file !== "string") {
    throw new ParityError(`${filePath}: golden file must be a mapping with file. Fix golden.yaml.`);
  }
  return loaded as unknown as GoldenFile;
}

function loadOptions(filePath: string): ScoreOptions {
  if (!existsSync(filePath)) return {};
  const loaded = readJson(filePath, "Fix options.json.");
  if (!isRecord(loaded)) {
    throw new ParityError(`${filePath}: options must be a JSON object. Fix options.json.`);
  }
  const decimal = loaded.decimal ?? ".";
  const order = loaded.dateOrder ?? loaded.date_order ?? "mdy";
  const tokenizer = loaded.tokenizer ?? "words";
  if (decimal !== "." && decimal !== ",") {
    throw new ParityError(`${filePath}: decimal must be "." or ",". Fix options.json.`);
  }
  if (order !== "mdy" && order !== "dmy") {
    throw new ParityError(`${filePath}: dateOrder must be mdy or dmy. Fix options.json.`);
  }
  if (typeof tokenizer !== "string" || tokenizer === "") {
    throw new ParityError(`${filePath}: tokenizer must be a non-empty string. Fix options.json.`);
  }
  return {
    decimal,
    dateOrder: order,
    tokenizer,
  };
}

function readJson(filePath: string, fix: string): unknown {
  let text: string;
  try {
    text = readFileSync(filePath, "utf8");
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new ParityError(`${filePath}: could not read JSON (${message}). ${fix}`);
  }
  try {
    return JSON.parse(text) as unknown;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new ParityError(`${filePath}: invalid JSON: ${message}. ${fix}`);
  }
}

function orderPairs(items: readonly (readonly string[])[]): [string, string][] {
  return items.map((pair, index) => {
    const before = pair[0];
    const after = pair[1];
    if (before === undefined || after === undefined) {
      throw new ParityError(
        `reading_order[${index}] must be [before, after]. Fix golden.yaml.`,
      );
    }
    return [before, after];
  });
}

function fieldMap(raw: Readonly<Record<string, unknown>>): Record<string, FieldValue> {
  const out: Record<string, FieldValue> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean" ||
      value === null
    ) {
      out[key] = value;
      continue;
    }
    throw new ParityError(
      `fields.${key} must be a string, number, boolean, or null. Fix golden.yaml.`,
    );
  }
  return out;
}

function readyFailures(failures: readonly Failure[]): Failure[] {
  return failures.map((failure) => readyValue(failure) as Failure);
}

function readyRecord(value: Record<string, unknown>): Record<string, unknown> {
  const ready = readyValue(value);
  return isRecord(ready) ? ready : {};
}

function readyValue(value: unknown): unknown {
  if (typeof value === "number") return whole(value);
  if (Array.isArray(value)) return value.map((item) => readyValue(item));
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = readyValue(item);
    return out;
  }
  return value;
}

function whole(value: number): number {
  if (Number.isFinite(value) && value === Math.trunc(value) && Math.abs(value) <= 2 ** 53) {
    return Math.trunc(value);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

declare const __filename: string | undefined;

function moduleFilename(): string {
  const url = import.meta.url;
  if (typeof url === "string" && url.length > 0) return fileURLToPath(url);
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  return process.argv[1] ?? process.cwd();
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invoked !== "" && invoked === path.resolve(moduleFilename())) {
  const code = main(process.argv.slice(2));
  if (code !== 0) process.exitCode = code;
}
