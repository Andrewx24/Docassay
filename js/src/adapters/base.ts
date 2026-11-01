/**
 * Adapter protocol, raw fetch result, and adapter errors.
 *
 * The Adapters doc splits every adapter into `fetch` (I/O) and `toDocument`
 * (pure mapping). Vendor SDKs are imported inside `fetch`, never at module load.
 *
 * @example
 * ```ts
 * import { AdapterError } from "./adapters/base.js";
 *
 * throw new AdapterError("vendor returned 503");
 * ```
 */

import type { Document, Source } from "../models/document.js";

export type { Document, Source };

/** Vendor payload plus the facts the runner copies onto `Document.meta`. */
export interface RawResult {
  payload: unknown;
  parserVersion: string;
  latencyMs: number;
  costUsd?: number;
}

/**
 * One parser. `id` is the name before any `:variant`.
 *
 * @example
 * ```ts
 * const adapter: Adapter = {
 *   id: "file",
 *   variants: [],
 *   available: async () => ({ ok: true }),
 *   cacheIdentity: () => ({}),
 *   fetch: async () => ({ payload: {}, parserVersion: "1", latencyMs: 0 }),
 *   toDocument: (_raw, source) => ({
 *     specVersion: "1.0.0",
 *     source,
 *     meta: { parser: "file", parserVersion: "1", adapterVersion: "0.0.0" },
 *     pages: [],
 *     blocks: [],
 *     tables: [],
 *   }),
 * };
 * ```
 */
export interface Adapter {
  readonly id: string;
  readonly variants: readonly string[];
  available(): Promise<{ ok: boolean; reason?: string }>;
  cacheIdentity(): Record<string, unknown>;
  fetch(path: string): Promise<RawResult>;
  toDocument(raw: RawResult, source: Source): Document;
}

/** A vendor or network failure while fetching one document. */
export class AdapterError extends Error {
  /**
   * @example
   * ```ts
   * throw new AdapterError("LlamaParse returned HTTP 503");
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "AdapterError";
  }
}

/** `available()` failed for one or more parsers. CLI commands exit 3. */
export class AdapterUnavailable extends Error {
  readonly exitCode = 3;

  /**
   * @example
   * ```ts
   * throw new AdapterUnavailable("docling: DOCLING_BASE_URL is not set");
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "AdapterUnavailable";
  }
}

/** Unknown adapter id or variant. CLI commands exit 2. */
export class AdapterConfigError extends Error {
  readonly exitCode = 2;

  /**
   * @example
   * ```ts
   * throw new AdapterConfigError('unknown parser id "nope"; valid ids: docling, file');
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "AdapterConfigError";
  }
}
