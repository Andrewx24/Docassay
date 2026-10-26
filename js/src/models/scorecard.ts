/* Generated from spec/scorecard.schema.json by scripts/gen-types.sh. Do not edit. */

export type Score = number;
export type MetricName = "tables" | "text" | "order" | "fields" | "chunks";

/**
 * Results of one docassay run. specVersion is const 1.0.0. Wire format is camelCase. Scores are in [0, 1] and are rounded to 4 decimal places only when written. additionalProperties is false. Metric names are tables, text, order, fields, chunks, and overall.
 */
export interface Scorecard {
  /**
   * Spec semver. const 1.0.0.
   */
  specVersion: "1.0.0";
  docassayVersion: string;
  /**
   * Random. Excluded from comparisons.
   */
  runId: string;
  /**
   * ISO 8601. Excluded from comparisons.
   */
  createdAt: string;
  parsers: ParserResult[];
}
export interface ParserResult {
  /**
   * Display name.
   */
  parser: string;
  /**
   * Adapter id.
   */
  adapter: string;
  parserVersion: string;
  summary: MetricScores;
  /**
   * Total cost for this parser.
   */
  costUsd?: number;
  latencyMsP50?: number;
  latencyMsP95?: number;
  documents: DocumentResult[];
}
/**
 * Mean over documents that have each metric.
 */
export interface MetricScores {
  tables?: Score;
  text?: Score;
  order?: Score;
  fields?: Score;
  chunks?: Score;
  overall?: Score;
}
export interface DocumentResult {
  /**
   * Golden id.
   */
  id: string;
  file: string;
  /**
   * Adapter failure. All metrics count as 0.
   */
  error?: string;
  scores: MetricScores1;
  failures: Failure[];
}
export interface MetricScores1 {
  tables?: Score;
  text?: Score;
  order?: Score;
  fields?: Score;
  chunks?: Score;
  overall?: Score;
}
export interface Failure {
  metric: MetricName;
  /**
   * For example tables[0].cells[1].
   */
  check: string;
  expected?: unknown;
  actual?: unknown;
  reason: string;
  page?: number;
}
