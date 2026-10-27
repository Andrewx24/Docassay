/* Generated from spec/document.schema.json by scripts/gen-types.sh. Do not edit. */

/**
 * [x0, y0, x1, y1] in PDF points. Origin is the top-left of the page.
 *
 * @minItems 4
 * @maxItems 4
 */
export type BBox = [number, number, number, number];

/**
 * One parsed file, normalized by an adapter. specVersion is const 1.0.0. Wire format is camelCase. additionalProperties is false except meta.extra, which is an open object, and fields, which is an open map of Field values.
 */
export interface Document {
  /**
   * Spec semver implemented by this document. const 1.0.0.
   */
  specVersion: "1.0.0";
  source: Source;
  meta: Meta;
  pages: Page[];
  /**
   * In reading order as the parser reports it.
   */
  blocks: Block[];
  tables: Table[];
  /**
   * Present only if the parser or a user pipeline chunked the document.
   */
  chunks?: Chunk[];
  /**
   * Present only for extraction parsers. Keys are field names.
   */
  fields?: {
    [k: string]: Field;
  };
  warnings?: string[];
}
export interface Source {
  /**
   * Path relative to the config file.
   */
  path: string;
  /**
   * Hash of the file bytes. Cache key.
   */
  sha256: string;
  mime: string;
  pageCount?: number;
}
export interface Meta {
  /**
   * Adapter id, for example llamaparse:agentic.
   */
  parser: string;
  /**
   * Vendor model or version that actually ran.
   */
  parserVersion: string;
  /**
   * docassay version of the adapter.
   */
  adapterVersion: string;
  latencyMs?: number;
  costUsd?: number;
  /**
   * ISO 8601. Excluded from cache identity and scoring.
   */
  createdAt?: string;
  /**
   * Open object. The only Document object that allows additional properties.
   */
  extra?: {
    [k: string]: unknown;
  };
}
export interface Page {
  /**
   * 1-based page number.
   */
  number: number;
  width?: number;
  height?: number;
}
export interface Block {
  /**
   * Stable within the document, for example b12.
   */
  id: string;
  type:
    | "title"
    | "heading"
    | "paragraph"
    | "list_item"
    | "caption"
    | "footnote"
    | "header"
    | "footer"
    | "code"
    | "formula"
    | "other";
  text: string;
  page: number;
  /**
   * Heading level. 1 is the top.
   */
  level?: number;
  bbox?: BBox;
}
export interface Table {
  id: string;
  /**
   * First page the table appears on. 1-based.
   */
  page: number;
  bbox?: BBox;
  /**
   * Header row, when the parser identified one. Row index 0 in scoring is the first body row.
   */
  header?: string[];
  /**
   * Body rows only. Ragged rows are padded with empty strings.
   */
  rows: string[][];
  /**
   * Parser's own rendering. Informational only.
   */
  markdown?: string;
}
export interface Chunk {
  id: string;
  text: string;
  /**
   * Block ids or table ids (t3) the chunk came from.
   */
  blockIds: string[];
  pages: number[];
  headingPath?: string[];
  tokens?: number;
}
export interface Field {
  value: string | number | boolean | null;
  /**
   * 0 to 1 when the parser reports it.
   */
  confidence?: number;
  citations?: Citation[];
}
export interface Citation {
  page: number;
  blockId?: string;
  bbox?: BBox;
  text?: string;
}
