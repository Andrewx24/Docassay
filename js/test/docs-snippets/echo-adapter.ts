import { readFile } from "node:fs/promises";

import { AdapterError, type Adapter, type RawResult } from "../../src/adapters/base.js";
import type { Document, Source } from "../../src/models/document.js";

/** Map a `{text}` payload to one paragraph. `fetch` reads the file as UTF-8. */
export function createEcho(variant: string | null, options: Record<string, unknown>): Adapter {
  return {
    id: "echo",
    variants: [],
    available: () => Promise.resolve({ ok: true }),
    cacheIdentity: () => ({ id: "echo", variant, options }),
    fetch: async (filePath: string): Promise<RawResult> => ({
      payload: { text: await readFile(filePath, "utf8") },
      parserVersion: "echo-1",
      latencyMs: 0,
    }),
    toDocument: (raw: RawResult, source: Source): Document => {
      const payload = raw.payload;
      const text =
        typeof payload === "object" && payload !== null && "text" in payload
          ? payload.text
          : undefined;
      if (typeof text !== "string") throw new AdapterError("payload.text must be a string");
      return {
        specVersion: "1.0.0",
        source,
        meta: { parser: "echo", parserVersion: raw.parserVersion, adapterVersion: "0.0.0" },
        pages: [],
        blocks: [{ id: "b1", type: "paragraph", text, page: 1 }],
        tables: [],
      };
    },
  };
}
