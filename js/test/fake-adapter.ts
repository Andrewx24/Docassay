/**
 * In-process adapter. `options.missing` makes `available()` fail.
 */
import type { Adapter, RawResult } from "../src/adapters/base.js";
import type { Document, Source } from "../src/models/document.js";
import type { AdapterFactory } from "../src/adapters/registry.js";

class DummyAdapter implements Adapter {
  readonly id = "dummy";
  readonly variants: readonly string[] = [];
  readonly variant: string | null;
  readonly options: Record<string, unknown>;

  constructor(variant: string | null, options: Record<string, unknown>) {
    this.variant = variant;
    this.options = options;
  }

  available(): Promise<{ ok: boolean; reason?: string }> {
    if (this.options.missing === true) return Promise.resolve({ ok: false, reason: "missing token" });
    return Promise.resolve({ ok: true, reason: "" });
  }

  cacheIdentity(): Record<string, unknown> {
    return { id: this.id, variant: this.variant, options: { ...this.options } };
  }

  fetch(filePath: string): Promise<RawResult> {
    void filePath;
    const cost = this.options.cost;
    return Promise.resolve({
      payload: { text: "hello" },
      parserVersion: "dummy-1",
      latencyMs: 12.5,
      costUsd: typeof cost === "number" ? cost : 0.01,
    });
  }

  toDocument(raw: RawResult, source: Source): Document {
    void raw;
    return {
      specVersion: "1.0.0",
      source,
      meta: { parser: "placeholder", parserVersion: "from-adapter", adapterVersion: "0" },
      pages: [],
      blocks: [],
      tables: [],
    };
  }
}

export const dummyFactory: AdapterFactory = Object.assign(
  (variant: string | null, options: Record<string, unknown>) => new DummyAdapter(variant, options),
  { id: "dummy", variants: [] as readonly string[] },
);
