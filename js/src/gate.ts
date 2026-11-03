/**
 * Save a baseline and fail the run when scores regress.
 *
 * Comparisons use scores rounded to 4 decimal places. Improvements never fail.
 * `docassay check` exits 1 when any check fails.
 *
 * @example
 * ```ts
 * import { compare, makeBaseline } from "./gate.js";
 *
 * makeBaseline(scorecard, new Date("2026-11-03T12:00:00Z"));
 * compare(scorecard, baseline, thresholds).exitCode;
 * ```
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { AdapterConfigError, AdapterUnavailable } from "./adapters/base.js";
import { CacheError } from "./cache.js";
import { ConfigError, loadConfig } from "./config.js";
import { GoldenError } from "./golden.js";
import type { Baseline } from "./models/baseline.js";
import type { Thresholds } from "./models/config.js";
import type { Scorecard } from "./models/scorecard.js";
import { formatCreated } from "./report/json.js";
import { RunError, execute } from "./runner.js";

const METRICS = ["tables", "text", "order", "fields", "chunks", "overall"] as const;
const MINUS = "\u2212";

/** A baseline file or scorecard that `check` cannot compare. */
export class GateError extends Error {
  readonly exitCode: number;

  /**
   * @example
   * ```ts
   * throw new GateError("baseline.json:1:1: baseline not found. Run `docassay baseline`.");
   * ```
   */
  constructor(message: string, exitCode = 2) {
    super(message);
    this.name = "GateError";
    this.exitCode = exitCode;
  }
}

/** Rendered check output and the process exit code. */
export interface GateResult {
  exitCode: number;
  text: string;
}

/**
 * Return `scorecard` as baseline JSON with failures removed.
 *
 * `pinnedAt` is the timestamp written to the file. Keys are sorted and the
 * file uses a 2-space indent.
 *
 * @example
 * ```ts
 * makeBaseline(scorecard, new Date("2026-11-03T00:00:00Z"));
 * ```
 */
export function makeBaseline(scorecard: Scorecard, pinnedAt: Date = new Date()): string {
  const data = asScorecard(scorecard);
  const payload: Record<string, unknown> = {
    specVersion: data.specVersion,
    docassayVersion: data.docassayVersion,
    runId: data.runId,
    createdAt: formatCreated(data.createdAt),
    pinnedAt: formatCreated(pinnedAt),
    parsers: parsersOf(data).map((parser) => baselineParser(parser)),
  };
  return stableJson(payload);
}

/**
 * Compare `scorecard` to `baseline` and return the check text.
 *
 * A score fails when its 4-decimal value is lower than the baseline by more
 * than the metric's `drop`, or when it is below `min`. `summaryOnly` skips
 * every per-document check.
 *
 * @example
 * ```ts
 * compare(scorecard, baseline, thresholds).exitCode;
 * ```
 */
export function compare(
  scorecard: Scorecard,
  baseline: Baseline,
  thresholds?: Thresholds,
  summaryOnly = false,
): GateResult {
  const current = asScorecard(scorecard);
  const reference = asBaseline(baseline);
  const lines: string[] = [];
  let failures = 0;
  let warnings = 0;
  const seen = new Set<string>();
  const referenceByName = new Map(parsersOf(reference).map((parser) => [parser.parser, parser]));
  for (const parser of parsersOf(current)) {
    seen.add(parser.parser);
    const previous = referenceByName.get(parser.parser);
    if (previous === undefined) {
      lines.push(`WARN  ${parser.parser} · new parser, no baseline`);
      warnings += 1;
      continue;
    }
    failures += summaryLines(lines, parser.parser, parser, previous, thresholds);
    if (summaryOnly) continue;
    const docs = documentLines(lines, parser.parser, parser, previous, thresholds);
    failures += docs.failures;
    warnings += docs.warnings;
    warnings += missingDocuments(lines, parser.parser, parser, previous);
  }
  for (const parser of parsersOf(reference)) {
    if (!seen.has(parser.parser)) {
      lines.push(`WARN  ${parser.parser} · missing from this run`);
      warnings += 1;
    }
  }
  const footer =
    `${count(failures, "regression", "regressions")} · ` +
    `${count(warnings, "warning", "warnings")} · ` +
    "run `docassay baseline` after an intended change";
  const body = lines.length > 0 ? `${lines.join("\n")}\n\n` : "";
  return { exitCode: failures > 0 ? 1 : 0, text: `${body}${footer}\n` };
}

/**
 * Run the suite and write `.docassay/baseline.json`. Return the exit code.
 *
 * @example
 * ```ts
 * await baselineCommand("docassay.yaml");
 * ```
 */
export async function baselineCommand(
  configPath: string,
  options: RunFlags & { pinnedAt?: Date } = {},
): Promise<number> {
  try {
    const loaded = loadConfig(configPath);
    const card = await runSuite(configPath, loaded, options);
    const target = baselineFile(configPath, loaded.baseline, options.baseline);
    const text = makeBaseline(card, options.pinnedAt ?? new Date());
    mkdirSync(path.dirname(path.resolve(target)), { recursive: true });
    writeFileSync(target, text, "utf8");
    process.stdout.write(`wrote ${target.split(path.sep).join("/")}\n`);
    return 0;
  } catch (err) {
    return commandError(err);
  }
}

/**
 * Run the suite, compare it to the baseline, and return the exit code.
 *
 * Exit 1 when a score regresses or a document starts erroring. Exit 2 when
 * the baseline or config cannot be read. Warnings do not fail the run.
 *
 * @example
 * ```ts
 * await checkCommand("docassay.yaml", { summaryOnly: true });
 * ```
 */
export async function checkCommand(configPath: string, options: RunFlags = {}): Promise<number> {
  try {
    const loaded = loadConfig(configPath);
    const target = baselineFile(configPath, loaded.baseline, options.baseline);
    let raw: string;
    try {
      raw = readFileSync(target, "utf8");
    } catch {
      throw new GateError(
        `${target.split(path.sep).join("/")}:1:1: baseline not found. Run \`docassay baseline\` to create it.`,
      );
    }
    let loadedBaseline: unknown;
    try {
      loadedBaseline = JSON.parse(raw) as unknown;
    } catch (err) {
      const message = err instanceof SyntaxError ? err.message : "invalid JSON";
      throw new GateError(
        `${target.split(path.sep).join("/")}:1:1: invalid JSON: ${message}. Fix the file or run \`docassay baseline\` again.`,
      );
    }
    const card = await runSuite(configPath, loaded, options);
    const result = compare(card, asBaseline(loadedBaseline), loaded.thresholds, options.summaryOnly === true);
    process.stdout.write(result.text);
    return result.exitCode;
  } catch (err) {
    return commandError(err);
  }
}

interface RunFlags {
  baseline?: string;
  summaryOnly?: boolean;
  parsers?: readonly string[];
  tags?: readonly string[];
  noCache?: boolean;
  refresh?: boolean;
  concurrency?: number;
  verbose?: boolean;
}

async function runSuite(
  configPath: string,
  loaded: ReturnType<typeof loadConfig>,
  options: RunFlags,
): Promise<Scorecard> {
  const policy = options.refresh === true ? "refresh" : options.noCache === true ? "no-cache" : "use";
  const outcome = await execute(loaded, {
    configDir: path.dirname(path.resolve(configPath)),
    parsers: options.parsers === undefined ? undefined : [...options.parsers],
    tags: options.tags === undefined ? undefined : [...options.tags],
    cachePolicy: policy,
    concurrency: options.concurrency,
    verbose: options.verbose,
  });
  return outcome.card;
}

function baselineFile(configPath: string, configured: string | undefined, override: string | undefined): string {
  if (override !== undefined) return override;
  return path.join(path.dirname(configPath), configured ?? ".docassay/baseline.json");
}

function commandError(err: unknown): number {
  if (err instanceof GateError) {
    process.stdout.write(`${err.message}\n`);
    return err.exitCode;
  }
  if (
    err instanceof RunError ||
    err instanceof ConfigError ||
    err instanceof GoldenError ||
    err instanceof AdapterConfigError ||
    err instanceof CacheError
  ) {
    process.stdout.write(`${err.message}\n`);
    return 2;
  }
  if (err instanceof AdapterUnavailable) {
    process.stdout.write(`${err.message}\n`);
    return 3;
  }
  if (err instanceof Error && "code" in err) {
    process.stdout.write(`could not write the baseline: ${err.message}\n`);
    return 2;
  }
  throw err;
}

function summaryLines(
  lines: string[],
  name: string,
  parser: ParserShape,
  previous: ParserShape,
  thresholds: Thresholds | undefined,
): number {
  let failures = 0;
  const earlier = scoreMap(previous.summary);
  for (const [metric, value] of scorePairs(parser.summary)) {
    const before = earlier[metric];
    const { drop, floor } = limits(thresholds, metric);
    if (before !== undefined && regressed(value, before, drop)) {
      lines.push(dropLine(name, "summary", metric, before, value, drop));
      failures += 1;
    }
    if (floor !== undefined && below(value, floor)) {
      lines.push(floorLine(name, "summary", metric, before ?? value, value, floor));
      failures += 1;
    }
  }
  return failures;
}

function documentLines(
  lines: string[],
  name: string,
  parser: ParserShape,
  previous: ParserShape,
  thresholds: Thresholds | undefined,
): { failures: number; warnings: number } {
  let failures = 0;
  let warnings = 0;
  const earlierDocs = new Map(documentsOf(previous).map((doc) => [doc.id, doc]));
  for (const doc of documentsOf(parser)) {
    const earlier = earlierDocs.get(doc.id);
    if (earlier === undefined) {
      lines.push(`WARN  ${name} · ${doc.id} · new document`);
      warnings += 1;
      continue;
    }
    if (doc.error !== undefined && doc.error !== "" && (earlier.error === undefined || earlier.error === "")) {
      lines.push(`FAIL  ${name} · ${doc.id} · error`);
      lines.push(`      document now errors: ${doc.error}`);
      failures += 1;
    }
    const previousScores = scoreMap(earlier.scores);
    for (const [metric, value] of scorePairs(doc.scores)) {
      const before = previousScores[metric];
      const { drop, floor } = limits(thresholds, metric);
      const dropped = before !== undefined && regressed(value, before, drop);
      const floored = floor !== undefined && below(value, floor);
      const detail = dropped || floored ? firstFailure(doc, metric) : undefined;
      if (dropped && before !== undefined) {
        lines.push(dropLine(name, doc.id, metric, before, value, drop));
        if (detail !== undefined) lines.push(detail);
        failures += 1;
      }
      if (floored && floor !== undefined) {
        lines.push(floorLine(name, doc.id, metric, before ?? value, value, floor));
        if (detail !== undefined && !dropped) lines.push(detail);
        failures += 1;
      }
    }
  }
  return { failures, warnings };
}

function missingDocuments(lines: string[], name: string, parser: ParserShape, previous: ParserShape): number {
  const present = new Set(documentsOf(parser).map((doc) => doc.id));
  let warnings = 0;
  for (const doc of documentsOf(previous)) {
    if (!present.has(doc.id)) {
      lines.push(`WARN  ${name} · ${doc.id} · missing from this run`);
      warnings += 1;
    }
  }
  return warnings;
}

function dropLine(parser: string, scope: string, metric: string, before: number, after: number, drop: number): string {
  const delta = units(after) - units(before);
  return (
    `FAIL  ${parser} · ${scope} · ${metric} ${fmt(before)} → ${fmt(after)} ` +
    `(${signed(delta)}, allowed ${signed(-units(drop))})`
  );
}

function floorLine(parser: string, scope: string, metric: string, before: number, after: number, floor: number): string {
  return `FAIL  ${parser} · ${scope} · ${metric} ${fmt(before)} → ${fmt(after)} (below floor ${fmt(floor)})`;
}

function firstFailure(doc: DocShape, metric: string): string | undefined {
  for (const failure of doc.failures ?? []) {
    if (failure.metric !== metric || failure.check === "") continue;
    const parts: string[] = [];
    if ("expected" in failure) parts.push(`expected ${JSON.stringify(failure.expected)}`);
    if ("actual" in failure) parts.push(`got ${JSON.stringify(failure.actual)}`);
    const body = parts.length > 0 ? parts.join(", ") : failure.reason;
    return `      ${failure.check}: ${body}`;
  }
  return undefined;
}

function limits(thresholds: Thresholds | undefined, metric: string): { drop: number; floor?: number } {
  const specific = thresholds?.[metric as keyof Thresholds];
  const fallback = thresholds?.default;
  const drop = specific?.drop ?? fallback?.drop ?? 0.02;
  const floor = specific?.min ?? fallback?.min;
  return floor === undefined ? { drop } : { drop, floor };
}

function regressed(current: number, baseline: number, drop: number): boolean {
  return units(current) < units(baseline) - units(drop);
}

function below(current: number, floor: number): boolean {
  return units(current) < units(floor);
}

function units(value: number): number {
  return Math.floor(value * 10000 + 0.5);
}

function fmt(value: number): string {
  return (units(value) / 10000).toFixed(4);
}

function signed(value: number): string {
  const number = (Math.abs(value) / 10000).toFixed(4);
  if (value < 0) return `${MINUS}${number}`;
  if (value > 0) return `+${number}`;
  return "0.0000";
}

function count(value: number, singular: string, plural: string): string {
  return `${value} ${value === 1 ? singular : plural}`;
}

interface ParserShape {
  parser: string;
  adapter: string;
  parserVersion: string;
  summary: Record<string, number>;
  documents: DocShape[];
  costUsd?: number;
  latencyMsP50?: number;
  latencyMsP95?: number;
}

interface DocShape {
  id: string;
  file: string;
  error?: string;
  scores: Record<string, number>;
  failures?: Array<{ metric: string; check: string; expected?: unknown; actual?: unknown; reason: string }>;
}

function scorePairs(value: unknown): Array<[string, number]> {
  const scores = scoreMap(value);
  return METRICS.filter((name) => name in scores).map((name) => [name, scores[name] ?? 0]);
}

function scoreMap(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  const found: Record<string, number> = {};
  for (const name of METRICS) {
    const item = value[name];
    if (typeof item === "number") found[name] = item;
  }
  return found;
}

function parsersOf(data: { parsers?: unknown }): ParserShape[] {
  if (!Array.isArray(data.parsers)) throw new GateError("scorecard parsers must be a list. Fix the JSON and retry.");
  return data.parsers.filter(isRecord).map((parser) => ({
    parser: String(parser.parser),
    adapter: String(parser.adapter),
    parserVersion: String(parser.parserVersion),
    summary: scoreMap(parser.summary),
    documents: documentsOf(parser),
    ...(typeof parser.costUsd === "number" ? { costUsd: parser.costUsd } : {}),
    ...(typeof parser.latencyMsP50 === "number" ? { latencyMsP50: parser.latencyMsP50 } : {}),
    ...(typeof parser.latencyMsP95 === "number" ? { latencyMsP95: parser.latencyMsP95 } : {}),
  }));
}

function documentsOf(parser: { documents?: unknown }): DocShape[] {
  if (!Array.isArray(parser.documents)) return [];
  return parser.documents.filter(isRecord).map((doc) => ({
    id: String(doc.id),
    file: String(doc.file),
    ...(typeof doc.error === "string" && doc.error !== "" ? { error: doc.error } : {}),
    scores: scoreMap(doc.scores),
    ...(Array.isArray(doc.failures) ? { failures: doc.failures as DocShape["failures"] } : {}),
  }));
}

function asScorecard(value: unknown): Scorecard {
  if (!isRecord(value) || value.specVersion !== "1.0.0" || !Array.isArray(value.parsers)) {
    throw new GateError("scorecard does not match scorecard.schema.json: parsers is required.");
  }
  return value as unknown as Scorecard;
}

function asBaseline(value: unknown): Baseline {
  if (!isRecord(value) || value.specVersion !== "1.0.0" || typeof value.pinnedAt !== "string") {
    throw new GateError("baseline does not match baseline.schema.json: pinnedAt is required.");
  }
  return value as unknown as Baseline;
}

function baselineParser(parser: ParserShape): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    parser: parser.parser,
    adapter: parser.adapter,
    parserVersion: parser.parserVersion,
    summary: parser.summary,
    documents: parser.documents.map((doc) => {
      const item: Record<string, unknown> = { id: doc.id, file: doc.file, scores: doc.scores };
      if (doc.error !== undefined) item.error = doc.error;
      return item;
    }),
  };
  if (parser.costUsd !== undefined) payload.costUsd = parser.costUsd;
  if (parser.latencyMsP50 !== undefined) payload.latencyMsP50 = parser.latencyMsP50;
  if (parser.latencyMsP95 !== undefined) payload.latencyMsP95 = parser.latencyMsP95;
  return payload;
}

function stableJson(value: unknown): string {
  return `${JSON.stringify(canonicalize(value), null, 2)}\n`;
}

function canonicalize(value: unknown): unknown {
  if (typeof value === "number" && Number.isFinite(value) && value === Math.trunc(value) && Math.abs(value) <= 2 ** 53) {
    return Math.trunc(value);
  }
  if (Array.isArray(value)) return value.map((item) => canonicalize(item));
  if (isRecord(value)) {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) sorted[key] = canonicalize(value[key]);
    return sorted;
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
