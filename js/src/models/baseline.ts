/* Generated from spec/baseline.schema.json by scripts/gen-types.sh. Do not edit. */

export type Score = number;

/**
 * A scorecard saved as the reference for docassay check. specVersion is const 1.0.0. Wire format is camelCase. documents[].failures is omitted. pinnedAt is the time the baseline was written. additionalProperties is false.
 */
export interface Baseline {
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
  /**
   * ISO 8601 time this baseline was written.
   */
  pinnedAt: string;
  parsers: ParserResult[];
}
export interface ParserResult {
  parser: string;
  adapter: string;
  parserVersion: string;
  summary: MetricScores;
  costUsd?: number;
  latencyMsP50?: number;
  latencyMsP95?: number;
  documents: DocumentResult[];
}
export interface MetricScores {
  tables?: Score;
  text?: Score;
  order?: Score;
  fields?: Score;
  chunks?: Score;
  overall?: Score;
}
/**
 * Scorecard document result with failures removed.
 */
export interface DocumentResult {
  id: string;
  file: string;
  error?: string;
  scores: MetricScores;
}
