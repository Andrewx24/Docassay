/* Generated from spec/golden.schema.json by scripts/gen-types.sh. Do not edit. */

/**
 * What correct parser output looks like for one source file. specVersion is const 1.0.0 (carried by Documents and scorecards, not by this file). Humans write this as YAML with snake_case keys. additionalProperties is false except fields, whose keys are open and whose values are string, number, boolean, or null. Cell, number, and date matching follows the Normalization doc; labels keep the text as printed on the page.
 */
export interface GoldenFile {
  /**
   * Source path relative to this golden file.
   */
  file: string;
  /**
   * Defaults to the file stem when omitted.
   */
  id?: string;
  /**
   * Used for filtering and grouping.
   */
  tags?: string[];
  expect?: Expect;
}
/**
 * Every section is optional. A scorer runs only if its section is present.
 */
export interface Expect {
  tables?: TableExpect[];
  text_contains?: string[];
  /**
   * Each item is a pair [before, after] of non-empty strings.
   */
  reading_order?: [string, string][];
  /**
   * Open map. The only object in this schema that allows additional properties. Values are string, number, boolean, or null.
   */
  fields?: {
    [k: string]: string | number | boolean | null;
  };
  chunks?: ChunkExpect;
}
export interface TableExpect {
  page: number;
  header?: string[];
  /**
   * Expected body-row count, not the rows themselves.
   */
  rows?: number;
  cols?: number;
  /**
   * Spot-checked cells. Row indexes are 0-based body rows.
   */
  cells?: CellExpect[];
}
export interface CellExpect {
  row: number;
  /**
   * Header name or 0-based column index.
   */
  col: number | string;
  /**
   * Text as it appears on the page, before normalization.
   */
  value: string;
}
export interface ChunkExpect {
  max_tokens?: number;
  min_tokens?: number;
  no_split_tables?: boolean;
  heading_paths?: {
    /**
     * A chunk containing this text must have path.
     */
    text: string;
    /**
     * @minItems 1
     */
    path: [string, ...string[]];
  }[];
}
