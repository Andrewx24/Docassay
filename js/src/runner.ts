/**
 * Run parsers, score golden files, and build a scorecard.
 *
 * The file adapter is not cached (`cacheable` is false). Every other adapter
 * reads and writes the parser cache from ALI-341. Paths inside the config are
 * relative to the config file; pass `configDir` when the process working
 * directory is somewhere else.
 *
 * @example
 * ```ts
 * import { loadConfig, run } from "docassay";
 *
 * const card = await run(await loadConfig("docassay.yaml"));
 * ```
 */

import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import {
  AdapterConfigError,
  AdapterError,
  AdapterUnavailable,
  type Adapter,
} from "./adapters/base.js";
import * as adapterRegistry from "./adapters/registry.js";
import { ensureAvailable, packageVersion, runParser } from "./adapters/runner_support.js";
import {
  type CachePolicy,
  CacheError,
  fileSha256,
  lookupCache,
  writeCache,
} from "./cache.js";
import { ConfigError, loadConfig } from "./config.js";
import { GoldenError, loadGolden } from "./golden.js";
import type { Config, Parser } from "./models/config.js";
import type { Document } from "./models/document.js";
import type { Expect, GoldenFile } from "./models/golden.js";
import type { Failure, Scorecard } from "./models/scorecard.js";
import { renderHtml } from "./report/html.js";
import { renderJson } from "./report/json.js";
import { renderMarkdown } from "./report/markdown.js";
import { renderTerminal } from "./report/terminal.js";
import { aggregateScores, documentScores, percentile } from "./scoring/aggregate.js";
import { scoreChunks, scoreFields, scoreOrder, scoreTables, scoreText } from "./scoring/index.js";
import type { Failure as ScoreFailure, ScoreOptions } from "./scoring/index.js";

const FORMATS = ["terminal", "json", "md", "html"] as const;
type Format = (typeof FORMATS)[number];

/** A usage problem found while starting a run. CLI commands exit 2. */
export class RunError extends Error {
  readonly exitCode = 2;

  /**
   * @example
   * ```ts
   * throw new RunError('unknown format "xml". Use terminal, json, md, or html.');
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "RunError";
  }
}

/** Filters, cache policy, and clocks for {@link run}. */
export interface RunOptions {
  /** Directory that contains the config file. */
  configDir?: string;
  /** Parser ids to keep. Empty means every parser in the config. */
  parsers?: readonly string[];
  /** Golden tags to keep. Empty means every file. A file matches any tag. */
  tags?: readonly string[];
  /** `use`, `no-cache`, or `refresh`. */
  cachePolicy?: CachePolicy;
  /** Parallel parse calls per parser. */
  concurrency?: number;
  /** Print cache hits while loading. */
  verbose?: boolean;
  /** Stable id for tests. Omitted values are random. */
  runId?: string;
  /** Stable clock for tests. Omitted values are the current time. */
  createdAt?: Date;
}

/** A scorecard plus the page totals and tags the reporters need. */
export interface RunOutcome {
  card: Scorecard;
  pages: number[];
  tags: Record<string, readonly string[]>;
}

interface DocOutcome {
  docId: string;
  file: string;
  scores: Record<string, number>;
  failures: Failure[];
  error?: string;
  pages: number;
  cost?: number;
  latency?: number;
  version?: string;
}

/**
 * Score every selected parser and return the scorecard.
 *
 * Paths in `config` are relative to the config file. Pass `options.configDir`
 * when the working directory is somewhere else. `runId` and `createdAt` change
 * on every call unless `options` sets them.
 *
 * @example
 * ```ts
 * const card = await run(await loadConfig("docassay.yaml"));
 * ```
 */
export async function run(config: Config, options: RunOptions = {}): Promise<Scorecard> {
  const outcome = await execute(config, options);
  return outcome.card;
}

/**
 * Score parsers and keep the page totals and tags used by reporters.
 *
 * @example
 * ```ts
 * const outcome = await execute(await loadConfig("docassay.yaml"), { verbose: true });
 * ```
 */
export async function execute(config: Config, options: RunOptions = {}): Promise<RunOutcome> {
  checkOptions(options);
  const base = path.resolve(options.configDir ?? process.cwd());
  const selected = selectParsers(config, options.parsers ?? []);
  const adapters = buildAdapters(selected, base);
  await ensureAvailable(adapters.map(([parser, adapter]) => [parser.id, adapter] as const));
  const goldenDir = path.resolve(base, config.golden);
  const files = selectFiles(loadGolden(goldenDir), options.tags ?? []);
  const cacheRoot = path.resolve(base, config.cache ?? ".docassay/cache");
  const scoreOptions = scoreOpts(config);
  const moment = options.createdAt ?? new Date();
  const runId = options.runId ?? newRunId();
  if (runId.length === 0) throw new RunError("run id must not be empty. Omit runId to generate one.");
  const tags: Record<string, readonly string[]> = {};
  for (const file of files) tags[docId(file)] = file.tags ?? [];
  const parsers: Scorecard["parsers"] = [];
  const pageTotals: number[] = [];
  for (const [parser, adapter] of adapters) {
    const outcomes = await mapPool(files, options.concurrency ?? 4, (golden) =>
      scoreOne(parser, adapter, golden, goldenDir, cacheRoot, options.cachePolicy ?? "use", options.verbose === true, scoreOptions),
    );
    parsers.push(parserPayload(parser, outcomes));
    pageTotals.push(outcomes.reduce((total, item) => total + item.pages, 0));
  }
  return {
    card: {
      specVersion: "1.0.0",
      docassayVersion: packageVersion(),
      runId,
      createdAt: moment.toISOString(),
      parsers,
    },
    pages: pageTotals,
    tags,
  };
}

/**
 * Print a scorecard and return the `docassay run` exit code.
 *
 * Exit 0 when the run finished and at least one document succeeded for every
 * parser. Exit 2 for usage, config, or golden-file errors. Exit 3 when a
 * parser is unavailable or every document errored for a parser. `--refresh`
 * wins over `--no-cache` when both are set.
 *
 * @example
 * ```ts
 * await runCommand("docassay.yaml", { format: "json" });
 * ```
 */
export async function runCommand(
  configPath: string,
  options: {
    parsers?: readonly string[];
    tags?: readonly string[];
    format?: string;
    out?: string;
    noCache?: boolean;
    refresh?: boolean;
    concurrency?: number;
    verbose?: boolean;
  } = {},
): Promise<number> {
  const format = options.format ?? "terminal";
  if (!FORMATS.includes(format as Format)) {
    process.stdout.write(`unknown format "${format}". Use terminal, json, md, or html.\n`);
    return 2;
  }
  const policy: CachePolicy = options.refresh ? "refresh" : options.noCache ? "no-cache" : "use";
  try {
    const config = loadConfig(configPath);
    const outcome = await execute(config, {
      configDir: path.dirname(path.resolve(configPath)),
      parsers: options.parsers,
      tags: options.tags,
      cachePolicy: policy,
      concurrency: options.concurrency,
      verbose: options.verbose,
    });
    const text = render(outcome, format, options.verbose === true);
    if (options.out === undefined) process.stdout.write(text);
    else {
      mkdirSync(path.dirname(path.resolve(options.out)), { recursive: true });
      writeFileSync(options.out, text, "utf8");
    }
    return exitCode(outcome.card);
  } catch (err) {
    if (err instanceof RunError || err instanceof ConfigError || err instanceof GoldenError) {
      process.stdout.write(`${err.message}\n`);
      return 2;
    }
    if (err instanceof AdapterConfigError || err instanceof CacheError) {
      process.stdout.write(`${err.message}\n`);
      return 2;
    }
    if (err instanceof AdapterUnavailable) {
      process.stdout.write(`${err.message}\n`);
      return 3;
    }
    throw err;
  }
}

function render(outcome: RunOutcome, format: string, verbose: boolean): string {
  if (format === "json") return renderJson(outcome.card);
  if (format === "md") return renderMarkdown(outcome.card, outcome.pages);
  if (format === "html") return renderHtml(outcome.card, outcome.pages, outcome.tags);
  return renderTerminal(outcome.card, outcome.pages, verbose);
}

function exitCode(card: Scorecard): number {
  for (const parser of card.parsers) {
    if (parser.documents.length > 0 && parser.documents.every((document) => document.error)) return 3;
  }
  return 0;
}

function checkOptions(options: RunOptions): void {
  const concurrency = options.concurrency ?? 4;
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RunError("concurrency must be an integer >= 1. Pass --concurrency 4.");
  }
  const policy = options.cachePolicy ?? "use";
  if (policy !== "use" && policy !== "no-cache" && policy !== "refresh") {
    throw new RunError('unknown cache policy. Use "use", "no-cache", or "refresh".');
  }
}

function selectParsers(config: Config, requested: readonly string[]): Parser[] {
  if (requested.length === 0) return [...config.parsers];
  const byId = new Map(config.parsers.map((parser) => [parser.id, parser]));
  const chosen: Parser[] = [];
  for (const parserId of requested) {
    const found = byId.get(parserId);
    if (found === undefined) {
      const known = config.parsers.map((parser) => parser.id).join(", ");
      throw new RunError(`unknown parser "${parserId}". Configured parsers: ${known}.`);
    }
    chosen.push(found);
  }
  return chosen;
}

function buildAdapters(parsers: readonly Parser[], base: string): Array<[Parser, Adapter]> {
  return parsers.map((parser) => {
    const options = { ...(parser.options ?? {}) };
    if (parser.id.split(":")[0] === "file") {
      const directory = options.dir;
      if (typeof directory === "string" && directory.length > 0) {
        options.dir = path.resolve(base, directory);
      }
    }
    return [parser, adapterRegistry.resolve(parser.id, options)] as [Parser, Adapter];
  });
}

function selectFiles(files: readonly GoldenFile[], tags: readonly string[]): GoldenFile[] {
  if (files.length === 0) {
    throw new RunError("golden set has no files. Add a *.yaml file under the golden folder.");
  }
  if (tags.length === 0) return [...files];
  const wanted = new Set(tags);
  const chosen = files.filter((file) => (file.tags ?? []).some((tag) => wanted.has(tag)));
  if (chosen.length === 0) {
    throw new RunError(
      `no golden file has tag ${tags.join(", ")}. Remove --tag or add the tag to a golden file.`,
    );
  }
  return chosen;
}

function scoreOpts(config: Config): ScoreOptions {
  return {
    decimal: config.normalize?.decimal === "," ? "," : ".",
    dateOrder: config.normalize?.date_order === "dmy" ? "dmy" : "mdy",
    tokenizer: config.tokenizer ?? "words",
  };
}

async function scoreOne(
  parser: Parser,
  adapter: Adapter,
  golden: GoldenFile,
  goldenDir: string,
  cacheRoot: string,
  policy: CachePolicy,
  verbose: boolean,
  scoreOptions: ScoreOptions,
): Promise<DocOutcome> {
  const id = docId(golden);
  const defined = definedMetrics(golden.expect);
  const source = path.resolve(goldenDir, golden.file);
  try {
    const document = await loadDocument(adapter, parser.id, source, golden.file, cacheRoot, policy, verbose);
    const scored = scoreDocument(document, golden, scoreOptions);
    return {
      docId: id,
      file: golden.file,
      scores: scored.scores,
      failures: scored.failures,
      pages: pageCount(document),
      cost: document.meta.costUsd,
      latency: document.meta.latencyMs,
      version: document.meta.parserVersion,
    };
  } catch (err) {
    const message = fetchMessage(err);
    if (message === null) throw err;
    const zeros: Record<string, number> = {};
    for (const name of defined) zeros[name] = 0;
    return {
      docId: id,
      file: golden.file,
      scores: documentScores(zeros),
      failures: [],
      error: message,
      pages: 0,
    };
  }
}

async function loadDocument(
  adapter: Adapter,
  parserId: string,
  source: string,
  stored: string,
  cacheRoot: string,
  policy: CachePolicy,
  verbose: boolean,
): Promise<Document> {
  if (!isCacheable(adapter)) return runParser(adapter, source, parserId, stored);
  const digest = fileSha256(readFileSync(source));
  const identity = adapter.cacheIdentity();
  const lookup = lookupCache(cacheRoot, digest, parserId, identity, { policy, verbose });
  if (lookup.document !== null) return lookup.document;
  const document = await runParser(adapter, source, parserId, stored);
  writeCache(cacheRoot, digest, parserId, identity, document);
  return document;
}

function scoreDocument(
  document: Document,
  golden: GoldenFile,
  options: ScoreOptions,
): { scores: Record<string, number>; failures: Failure[] } {
  const expect = golden.expect;
  const raw: Record<string, number> = {};
  const failures: ScoreFailure[] = [];
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
      const result = scoreOrder(document, expect.reading_order, options);
      raw.order = result.score;
      failures.push(...result.failures);
    }
    if (expect.fields !== undefined) {
      const result = scoreFields(document, expect.fields, options);
      raw.fields = result.score;
      failures.push(...result.failures);
    }
    if (expect.chunks !== undefined) {
      const result = scoreChunks(document, expect.chunks, options);
      raw.chunks = result.score;
      failures.push(...result.failures);
    }
  }
  return { scores: documentScores(raw), failures: failures.map(copyFailure) };
}

function parserPayload(parser: Parser, outcomes: readonly DocOutcome[]): Scorecard["parsers"][number] {
  const versionText = outcomes.find((item) => item.version !== undefined)?.version ?? "unknown";
  const costs = outcomes.flatMap((item) => (item.cost === undefined ? [] : [item.cost]));
  const latencies = outcomes.flatMap((item) => (item.latency === undefined ? [] : [item.latency]));
  const payload: Scorecard["parsers"][number] = {
    parser: parser.name ?? parser.id,
    adapter: parser.id,
    parserVersion: versionText,
    summary: aggregateScores(outcomes.map((item) => item.scores)),
    documents: outcomes.map(documentPayload),
  };
  if (costs.length > 0) payload.costUsd = costs.reduce((total, value) => total + value, 0);
  if (latencies.length > 0) {
    payload.latencyMsP50 = percentile(latencies, 0.5);
    payload.latencyMsP95 = percentile(latencies, 0.95);
  }
  return payload;
}

function documentPayload(item: DocOutcome): Scorecard["parsers"][number]["documents"][number] {
  const payload: Scorecard["parsers"][number]["documents"][number] = {
    id: item.docId,
    file: item.file,
    scores: item.scores,
    failures: item.failures,
  };
  if (item.error !== undefined) payload.error = item.error;
  return payload;
}

function definedMetrics(expect: Expect | undefined): string[] {
  if (expect === undefined) return [];
  const found: string[] = [];
  if (expect.tables !== undefined) found.push("tables");
  if (expect.text_contains !== undefined) found.push("text");
  if (expect.reading_order !== undefined) found.push("order");
  if (expect.fields !== undefined) found.push("fields");
  if (expect.chunks !== undefined) found.push("chunks");
  return found;
}

function pageCount(document: Document): number {
  if (document.source.pageCount !== undefined) return document.source.pageCount;
  return document.pages.length;
}

function docId(golden: GoldenFile): string {
  if (golden.id !== undefined && golden.id.length > 0) return golden.id;
  return path.basename(golden.file, path.extname(golden.file));
}

function isCacheable(adapter: Adapter): boolean {
  const candidate = adapter as Adapter & { cacheable?: boolean };
  return candidate.cacheable !== false;
}

function fetchMessage(err: unknown): string | null {
  if (err instanceof AdapterError) return err.message.trim() || "adapter failed";
  if (err instanceof Error && "code" in err && typeof err.code === "string") {
    return err.message.trim() || "adapter failed";
  }
  return null;
}

function copyFailure(failure: ScoreFailure): Failure {
  const payload: Failure = { metric: failure.metric, check: failure.check, reason: failure.reason };
  if (failure.expected !== undefined) payload.expected = failure.expected;
  if (failure.actual !== undefined) payload.actual = failure.actual;
  if (failure.page !== undefined) payload.page = failure.page;
  return payload;
}

async function mapPool<T, R>(
  items: readonly T[],
  concurrency: number,
  worker: (item: T) => Promise<R>,
): Promise<R[]> {
  if (concurrency <= 1 || items.length <= 1) {
    const serial: R[] = [];
    for (const item of items) serial.push(await worker(item));
    return serial;
  }
  const results = new Array<R>(items.length);
  let next = 0;
  const runWorker = async (): Promise<void> => {
    while (next < items.length) {
      const index = next;
      next += 1;
      const item = items[index];
      if (item === undefined) return;
      results[index] = await worker(item);
    }
  };
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => runWorker());
  await Promise.all(workers);
  return results;
}

function newRunId(): string {
  return `run_${randomBytes(8).toString("hex")}`;
}
