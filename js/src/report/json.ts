/**
 * JSON scorecard.
 *
 * The object validates against `scorecard.schema.json`. `runId` and
 * `createdAt` are the only fields that change between identical runs. Integral
 * numbers are written without a trailing `.0` so Python and TypeScript match.
 *
 * @example
 * ```ts
 * import { renderJson } from "./report/json.js";
 *
 * renderJson(card);
 * ```
 */

import type { Failure, MetricScores, ParserResult, Scorecard } from "../models/scorecard.js";
import { metricValue } from "./terminal.js";
import { METRICS } from "../scoring/aggregate.js";

const SCORE_KEYS = [...METRICS, "overall"] as const;

/**
 * Return `card` as indented JSON with a trailing newline.
 *
 * @example
 * ```ts
 * const text = renderJson(card);
 * ```
 */
export function renderJson(card: Scorecard): string {
  return `${JSON.stringify(scorecardDict(card), null, 2)}\n`;
}

/**
 * Return `card` as JSON-ready data with stable key order.
 *
 * @example
 * ```ts
 * scorecardDict(card).specVersion === "1.0.0";
 * ```
 */
export function scorecardDict(card: Scorecard): Record<string, unknown> {
  return {
    specVersion: card.specVersion,
    docassayVersion: card.docassayVersion,
    runId: card.runId,
    createdAt: formatCreated(card.createdAt),
    parsers: card.parsers.map(parserDict),
  };
}

/**
 * Return `moment` as UTC `YYYY-MM-DDTHH:MM:SSZ`.
 *
 * `moment` is an ISO 8601 string or a Date.
 *
 * @example
 * ```ts
 * formatCreated("2026-10-26T15:00:00.000Z") === "2026-10-26T15:00:00Z";
 * ```
 */
export function formatCreated(moment: string | Date): string {
  const text = moment instanceof Date ? moment.toISOString() : new Date(moment).toISOString();
  const dot = text.indexOf(".");
  if (dot === -1) return text;
  return `${text.slice(0, dot)}Z`;
}

function parserDict(parser: ParserResult): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    parser: parser.parser,
    adapter: parser.adapter,
    parserVersion: parser.parserVersion,
    summary: scoresDict(parser.summary),
  };
  if (parser.costUsd !== undefined) payload.costUsd = jsonNumber(parser.costUsd);
  if (parser.latencyMsP50 !== undefined) payload.latencyMsP50 = jsonNumber(parser.latencyMsP50);
  if (parser.latencyMsP95 !== undefined) payload.latencyMsP95 = jsonNumber(parser.latencyMsP95);
  payload.documents = parser.documents.map((document) => {
    const row: Record<string, unknown> = { id: document.id, file: document.file };
    if (document.error) row.error = document.error;
    row.scores = scoresDict(document.scores);
    row.failures = document.failures.map(failureDict);
    return row;
  });
  return payload;
}

function scoresDict(scores: MetricScores): Record<string, number> {
  const payload: Record<string, number> = {};
  for (const name of SCORE_KEYS) {
    const value = metricValue(scores, name);
    if (value !== undefined) payload[name] = jsonNumber(value);
  }
  return payload;
}

function failureDict(failure: Failure): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    metric: failure.metric,
    check: failure.check,
    reason: failure.reason,
  };
  if ("expected" in failure && failure.expected !== undefined) payload.expected = jsonReady(failure.expected);
  if ("actual" in failure && failure.actual !== undefined) payload.actual = jsonReady(failure.actual);
  if (failure.page !== undefined) payload.page = failure.page;
  return payload;
}

function jsonReady(value: unknown): unknown {
  if (typeof value === "boolean" || value === null || typeof value === "string") return value;
  if (typeof value === "number") return jsonNumber(value);
  if (Array.isArray(value)) return value.map(jsonReady);
  if (typeof value === "object") {
    const payload: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) payload[key] = jsonReady(item);
    return payload;
  }
  return String(value);
}

function jsonNumber(value: number): number {
  if (Number.isFinite(value) && value === Math.trunc(value) && Math.abs(value) <= 2 ** 53) {
    return Math.trunc(value);
  }
  return value;
}
