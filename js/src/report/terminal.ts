/**
 * Terminal scorecard.
 *
 * Columns follow the CLI page: parser, the five metrics, overall, dollars per
 * 1,000 pages, and p95 latency. `—` is a metric no golden file labels for that
 * parser. Rows are sorted by overall descending, then parser name.
 *
 * @example
 * ```ts
 * import { renderTerminal } from "./report/terminal.js";
 *
 * console.log(renderTerminal(card, [3]));
 * ```
 */

import { version } from "../version.js";
import type { MetricScores, ParserResult, Scorecard } from "../models/scorecard.js";
import { METRICS } from "../scoring/aggregate.js";

export const EM_DASH = "\u2014";
const HEADERS = ["parser", ...METRICS, "overall", "$/1k pages", "p95"];
const RIGHT = new Set(["$/1k pages", "p95"]);

/**
 * Render `card` as the terminal table, worst-documents section, and failures.
 *
 * `pages` is one page total per parser, in the same order as `card.parsers`.
 * `verbose` prints every failure. Otherwise the last line points at `-v` when
 * anything scored below 1.
 *
 * @example
 * ```ts
 * renderTerminal(card, [1, 2], true);
 * ```
 */
export function renderTerminal(card: Scorecard, pages: readonly number[], verbose = false): string {
  const ordered = sortedParsers(card, pages);
  const count = card.parsers[0]?.documents.length ?? 0;
  const lines = [
    `docassay ${version} · ${plural(count, "document")} · ${plural(card.parsers.length, "parser")}`,
    "",
  ];
  lines.push(...renderColumns(HEADERS, ordered.map(([parser, pageCount]) => row(parser, pageCount))));
  const worst = worstLines(card);
  if (worst.length > 0) lines.push("", "Worst documents", ...worst);
  const failures = failureLines(ordered);
  if (verbose && failures.length > 0) lines.push("", "Failures", ...failures);
  else if (!verbose && (worst.length > 0 || failures.length > 0)) {
    lines.push("", "Run with -v for every failure.");
  }
  return `${lines.join("\n")}\n`;
}

/**
 * Return parsers and page totals sorted by overall descending, then name.
 *
 * @example
 * ```ts
 * sortedParsers(card, [3]);
 * ```
 */
export function sortedParsers(
  card: Scorecard,
  pages: readonly number[],
): Array<[ParserResult, number]> {
  if (pages.length !== card.parsers.length) {
    throw new Error(
      `page counts (${pages.length}) must match parsers (${card.parsers.length}). Pass one total per parser.`,
    );
  }
  const pairs = card.parsers.map((parser, index) => {
    const pageCount = pages[index] ?? 0;
    return [parser, pageCount] as [ParserResult, number];
  });
  return pairs.sort((left, right) => {
    const leftOverall = metricValue(left[0].summary, "overall");
    const rightOverall = metricValue(right[0].summary, "overall");
    const leftRank = leftOverall === undefined ? -1 : leftOverall;
    const rightRank = rightOverall === undefined ? -1 : rightOverall;
    if (leftRank !== rightRank) return rightRank - leftRank;
    if (left[0].parser < right[0].parser) return -1;
    if (left[0].parser > right[0].parser) return 1;
    return 0;
  });
}

/**
 * Return one score, or `undefined` when the metric is absent.
 *
 * @example
 * ```ts
 * metricValue(card.parsers[0].summary, "tables");
 * ```
 */
export function metricValue(scores: MetricScores, name: string): number | undefined {
  const value = scores[name as keyof MetricScores];
  return typeof value === "number" ? value : undefined;
}

/**
 * Format a score with two decimals, or an em dash when it is absent.
 *
 * @example
 * ```ts
 * formatScore(0.9) === "0.90";
 * ```
 */
export function formatScore(value: number | undefined): string {
  if (value === undefined) return EM_DASH;
  return roundDigits(value, 2).toFixed(2);
}

/**
 * Format a score with up to four decimals, or an em dash when it is absent.
 *
 * @example
 * ```ts
 * formatScore4(0.5) === "0.5";
 * ```
 */
export function formatScore4(value: number | undefined): string {
  if (value === undefined) return EM_DASH;
  let text = roundDigits(value, 4).toFixed(4);
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text === "" ? "0" : text;
}

/**
 * Format dollars with two decimals, or an em dash when cost is unknown.
 *
 * @example
 * ```ts
 * formatCost(0) === "0.00";
 * ```
 */
export function formatCost(value: number | undefined): string {
  if (value === undefined) return EM_DASH;
  return roundDigits(value, 2).toFixed(2);
}

/**
 * Format latency in seconds with one decimal, or an em dash when unknown.
 *
 * @example
 * ```ts
 * formatLatency(6100) === "6.1s";
 * ```
 */
export function formatLatency(milliseconds: number | undefined): string {
  if (milliseconds === undefined) return EM_DASH;
  return `${roundDigits(milliseconds / 1000, 1).toFixed(1)}s`;
}

/**
 * Return `cost * 1000 / pages`, or `undefined` when either side is missing.
 *
 * @example
 * ```ts
 * costPerThousand(0.02, 2) === 10;
 * ```
 */
export function costPerThousand(cost: number | undefined, pages: number): number | undefined {
  if (cost === undefined || pages <= 0) return undefined;
  return (cost * 1000) / pages;
}

/**
 * Return one line per document, using the metric with the lowest score.
 *
 * @example
 * ```ts
 * worstLines(card);
 * ```
 */
export function worstLines(card: Scorecard): string[] {
  const grouped = new Map<string, Array<[string, MetricScores]>>();
  for (const parser of card.parsers) {
    for (const document of parser.documents) {
      const rows = grouped.get(document.id) ?? [];
      rows.push([parser.parser, document.scores]);
      grouped.set(document.id, rows);
    }
  }
  const entries: Array<[number, string, string, Array<[string, number]>]> = [];
  for (const [docId, rows] of grouped) {
    let chosen: [number, string, Array<[string, number]>] | undefined;
    for (const metric of METRICS) {
      const pairs: Array<[string, number]> = [];
      for (const [name, scores] of rows) {
        const value = metricValue(scores, metric);
        if (value !== undefined) pairs.push([name, value]);
      }
      if (pairs.length === 0) continue;
      const lowest = Math.min(...pairs.map((pair) => pair[1]));
      if (chosen === undefined || lowest < chosen[0]) chosen = [lowest, metric, pairs];
    }
    if (chosen === undefined || chosen[0] >= 1) continue;
    const shown = chosen[2].filter((pair) => pair[1] < 1);
    shown.sort((left, right) => left[1] - right[1] || left[0].localeCompare(right[0]));
    if (shown.length > 0) entries.push([chosen[0], docId, chosen[1], shown]);
  }
  entries.sort((left, right) => left[0] - right[0] || left[1].localeCompare(right[1]));
  const width = entries.reduce((max, entry) => Math.max(max, entry[1].length), 0);
  return entries.map((entry) => {
    const body = entry[3].map((pair) => `${formatScore(pair[1])} (${pair[0]})`).join(", ");
    return `  ${entry[1].padEnd(width)}  ${entry[2]} ${body}`;
  });
}

function failureLines(ordered: ReadonlyArray<readonly [ParserResult, number]>): string[] {
  const lines: string[] = [];
  for (const [parser] of ordered) {
    for (const document of parser.documents) {
      if (document.error) lines.push(`  ${parser.parser}  ${document.id}  error  ${document.error}`);
      for (const failure of document.failures) {
        lines.push(
          `  ${parser.parser}  ${document.id}  ${failure.metric}  ${failure.check}  ${failure.reason}`,
        );
      }
    }
  }
  return lines;
}

function row(parser: ParserResult, pages: number): string[] {
  const cells = [parser.parser];
  for (const name of [...METRICS, "overall"]) cells.push(formatScore(metricValue(parser.summary, name)));
  cells.push(formatCost(costPerThousand(parser.costUsd, pages)));
  cells.push(formatLatency(parser.latencyMsP95));
  return cells;
}

function renderColumns(headers: readonly string[], rows: readonly (readonly string[])[]): string[] {
  const valueWidths = headers.map((_header, index) =>
    rows.reduce((max, line) => Math.max(max, line[index]?.length ?? 0), 0),
  );
  const widths = headers.map((header, index) => Math.max(header.length, valueWidths[index] ?? 0));
  const lines = [join(headers.map((header, index) => header.padEnd(widths[index] ?? 0)))];
  for (const line of rows) {
    const cells = line.map((value, index) => {
      const width = widths[index] ?? 0;
      const header = headers[index] ?? "";
      if (RIGHT.has(header)) return value.padStart(valueWidths[index] ?? 0).padEnd(width);
      return value.padEnd(width);
    });
    lines.push(join(cells));
  }
  return lines;
}

function join(cells: readonly string[]): string {
  return cells.join("  ").trimEnd();
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function roundDigits(value: number, digits: number): number {
  const scale = 10 ** digits;
  return Math.floor(value * scale + 0.5) / scale;
}
