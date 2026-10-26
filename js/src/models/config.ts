/* Generated from spec/config.schema.json by scripts/gen-types.sh. Do not edit. */

/**
 * A docassay.yaml file. specVersion is const 1.0.0 (the spec this config is written against; the file's own version field is the config format, const 1). Humans write this as YAML with snake_case keys. additionalProperties is false. parsers[].options is an open object because the Spec page passes options through to the adapter unchanged. Threshold metric names are the scorecard metrics.
 */
export interface Config {
  /**
   * Config format. Not specVersion.
   */
  version: 1;
  /**
   * Folder of *.yaml golden files, relative to this file.
   */
  golden: string;
  /**
   * Defaults to .docassay/cache.
   */
  cache?: string;
  /**
   * Defaults to .docassay/baseline.json.
   */
  baseline?: string;
  /**
   * @minItems 1
   */
  parsers: [Parser, ...Parser[]];
  thresholds?: Thresholds;
  normalize?: Normalize;
  /**
   * words, or tiktoken:<encoding> such as tiktoken:cl100k_base.
   */
  tokenizer?: "words" | string;
}
export interface Parser {
  /**
   * adapter or adapter:variant.
   */
  id: string;
  /**
   * Display name when one adapter is used twice.
   */
  name?: string;
  /**
   * Passed to the adapter as-is. Open object.
   */
  options?: {
    [k: string]: unknown;
  };
}
export interface Thresholds {
  default?: Threshold;
  tables?: Threshold;
  text?: Threshold;
  order?: Threshold;
  fields?: Threshold;
  chunks?: Threshold;
  overall?: Threshold;
}
export interface Threshold {
  /**
   * Fail if a score falls more than this far below the baseline.
   */
  drop?: number;
  /**
   * Absolute floor, regardless of the baseline.
   */
  min?: number;
}
export interface Normalize {
  /**
   * How to read an ambiguous numeric date such as 03/04/2026.
   */
  date_order?: "mdy" | "dmy";
  /**
   * Decimal separator.
   */
  decimal?: "." | ",";
}
