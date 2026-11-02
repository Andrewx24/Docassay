/**
 * Combine metric scores into an overall score.
 *
 * `doc.overall` is the mean of the metrics present on that document.
 * `summary[metric]` is the mean of that metric over documents that have it.
 * `summary.overall` is the mean of the document overalls. Cost and latency
 * stay out of every score.
 *
 * @example
 * ```ts
 * import { aggregateScores, documentScores } from "./scoring/aggregate.js";
 *
 * const scores = documentScores({ tables: 1, text: 0 });
 * aggregateScores([scores]);
 * ```
 */

import { round4 } from "../normalize/index.js";

export const METRICS = ["tables", "text", "order", "fields", "chunks"] as const;
export type ScoreMetric = (typeof METRICS)[number];

const SUMMARY = [...METRICS, "overall"] as const;

/**
 * Return the arithmetic mean of `values`.
 *
 * @example
 * ```ts
 * mean([1, 0]) === 0.5;
 * ```
 */
export function mean(values: readonly number[]): number {
  if (values.length === 0) throw new Error("mean requires at least one value");
  return values.reduce((total, value) => total + value, 0) / values.length;
}

/**
 * Return the linear percentile of `values`.
 *
 * `fraction` is between 0 and 1. One sample returns that sample. With more
 * samples the rank is `fraction * (count - 1)` and the result interpolates
 * the two surrounding values. Latency percentiles use this so Python and
 * TypeScript agree.
 *
 * @example
 * ```ts
 * percentile([10, 20, 30], 0.5) === 20;
 * ```
 */
export function percentile(values: readonly number[], fraction: number): number {
  if (values.length === 0) throw new Error("percentile requires at least one value");
  if (fraction < 0 || fraction > 1) throw new Error("percentile fraction must be between 0 and 1");
  const ordered = [...values].sort((left, right) => left - right);
  const only = ordered[0];
  if (ordered.length === 1) return only ?? 0;
  const rank = fraction * (ordered.length - 1);
  const low = Math.floor(rank);
  const high = Math.ceil(rank);
  const lower = ordered[low] ?? 0;
  if (low === high) return lower;
  const upper = ordered[high] ?? lower;
  const weight = rank - low;
  return lower * (1 - weight) + upper * weight;
}

/**
 * Copy present metrics and set `overall` to their mean, rounded to 4 decimals.
 *
 * A document with no metrics has no overall. An adapter failure passes 0 for
 * every metric the golden file defines, and the overall is then 0.
 *
 * @example
 * ```ts
 * documentScores({ tables: 1, text: 0 }).overall === 0.5;
 * ```
 */
export function documentScores(metricScores: Readonly<Record<string, number>>): Record<string, number> {
  const scores: Record<string, number> = {};
  for (const name of METRICS) {
    const value = metricScores[name];
    if (value !== undefined) scores[name] = value;
  }
  const present = METRICS.map((name) => scores[name]).filter((value): value is number => value !== undefined);
  if (present.length > 0) scores.overall = round4(mean(present));
  return scores;
}

/**
 * Mean each metric over the documents that have it.
 *
 * `overall` is the mean of document overalls, not the mean of the summary
 * metrics. Each mean is rounded to 4 decimals. An empty list returns `{}`.
 *
 * @example
 * ```ts
 * aggregateScores([{ tables: 1, overall: 1 }, { text: 0, overall: 0 }]);
 * ```
 */
export function aggregateScores(
  perDocument: readonly Readonly<Record<string, number>>[],
): Record<string, number> {
  const summary: Record<string, number> = {};
  for (const name of SUMMARY) {
    const found: number[] = [];
    for (const item of perDocument) {
      const value = item[name];
      if (value !== undefined) found.push(value);
    }
    if (found.length > 0) summary[name] = round4(mean(found));
  }
  return summary;
}
