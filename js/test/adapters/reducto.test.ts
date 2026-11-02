import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  API_KEY,
  ReductoAdapter,
  pollControl,
  reductoCostUsd,
  reductoToDocument,
} from "../../src/adapters/reducto.js";
import { resolve } from "../../src/adapters/registry.js";
import type { Document, Source } from "../../src/models/document.js";
import { version } from "../../src/version.js";

const ROOT = path.resolve(__dirname, "../../../spec/fixtures/adapters/reducto");
const SOURCE: Source = {
  path: "files/sample.pdf",
  sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  mime: "application/pdf",
};
const CASES = ["parse/invoice", "parse/edges", "extract/invoice", "extract/nested"];
const temps: string[] = [];
const savedKey = process.env.REDUCTO_API_KEY;
const savedBase = process.env.REDUCTO_BASE_URL;
const originalNow = pollControl.now;
const originalSleep = pollControl.sleep;
const originalFetch = globalThis.fetch;

afterEach(() => {
  pollControl.now = originalNow;
  pollControl.sleep = originalSleep;
  globalThis.fetch = originalFetch;
  if (savedKey === undefined) delete process.env.REDUCTO_API_KEY;
  else process.env.REDUCTO_API_KEY = savedKey;
  if (savedBase === undefined) delete process.env.REDUCTO_BASE_URL;
  else process.env.REDUCTO_BASE_URL = savedBase;
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function canonical(document: Document): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

function load(name: string): unknown {
  return JSON.parse(readFileSync(path.join(ROOT, `${name}.raw.json`), "utf8")) as unknown;
}

function readText(name: string): string {
  return readFileSync(path.join(ROOT, name), "utf8");
}

function pdf(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "reducto-"));
  temps.push(dir);
  const file = path.join(dir, "sample.pdf");
  writeFileSync(file, "%PDF");
  return file;
}

interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body?: string;
  form?: FormData;
}

function script(responses: Array<[number, unknown]>): Call[] {
  const calls: Call[] = [];
  const pending = [...responses];
  globalThis.fetch = async (input, init) => {
    const next = pending.shift();
    if (!next) throw new Error("unexpected fetch");
    const [status, body] = next;
    calls.push({
      method: init?.method ?? "GET",
      url: String(input),
      headers: headersOf(init?.headers),
      body: typeof init?.body === "string" ? init.body : undefined,
      form: init?.body instanceof FormData ? init.body : undefined,
    });
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, { status });
  };
  return calls;
}

function headersOf(headers: RequestInit["headers"]): Record<string, string> {
  const out: Record<string, string> = {};
  if (!headers || headers instanceof Headers || Array.isArray(headers)) return out;
  for (const [key, value] of Object.entries(headers)) out[key] = typeof value === "string" ? value : value.join(",");
  return out;
}

function useClock(): { slept: number[] } {
  const clock = { t: 0, slept: [] as number[] };
  pollControl.now = () => clock.t;
  pollControl.sleep = (seconds) => {
    clock.slept.push(seconds);
    clock.t += seconds;
    return Promise.resolve();
  };
  return clock;
}

describe("reducto", () => {
  it("matches the recorded documents", () => {
    for (const name of CASES) {
      const document = reductoToDocument(load(name), SOURCE, "r-1");
      expect(canonical(document)).toBe(readText(`${name}.doc.json`));
      expect(document.meta.parser).toBe("reducto");
      expect(document.meta.parserVersion).toBe("r-1");
      expect(document.meta.adapterVersion).toBe(version);
    }
  });

  it("maps invoice blocks, the table, and heading paths", () => {
    const document = reductoToDocument(load("parse/invoice"), SOURCE, "r-1");
    const byText = new Map(document.blocks.map((block) => [block.text, block]));
    expect(byText.get("Invoice")?.type).toBe("title");
    expect(byText.get("Invoice")?.bbox).toEqual([61.2, 39.6, 244.8, 71.28]);
    expect(byText.get("## Terms")?.level).toBe(2);
    expect(byText.get("• Net 30")?.type).toBe("list_item");
    expect(byText.get("Chart of spend")?.type).toBe("caption");
    expect(document.tables[0]?.header).toEqual(["Item", "Qty", "Price"]);
    expect(document.tables[0]?.rows).toEqual([
      ["Widget", "2", ""],
      ["Gadget", "1", "1,200.00"],
    ]);
    expect(document.chunks?.[0]?.headingPath).toEqual(["Invoice", "## Terms"]);
    expect(document.chunks?.[1]?.headingPath).toEqual(["Invoice", "## Late fees"]);
    expect(document.chunks?.[0]?.blockIds).toContain("t1");
    expect(document.fields).toBeUndefined();
  });

  it("scales boxes and omits a page with no size", () => {
    const document = reductoToDocument(load("parse/edges"), SOURCE, "r-1");
    expect(document.blocks.find((block) => block.text === "Scaled")?.bbox).toEqual([20, 80, 100, 100]);
    expect(document.blocks.find((block) => block.text === "☑ Agree")?.bbox).toBeUndefined();
    expect(document.tables[0]?.rows[0]).toEqual(["H", "A & B"]);
    expect(document.tables[1]?.header).toEqual(["A", "B", ""]);
    expect(document.blocks.find((block) => block.text === "Page two")?.bbox).toBeUndefined();
    expect(document.pages[1]?.width).toBeUndefined();
  });

  it("keeps extract citations and nested fields", () => {
    const invoice = reductoToDocument(load("extract/invoice"), SOURCE, "r-1");
    expect(invoice.fields?.vendor?.confidence).toBe(0.95);
    expect(invoice.fields?.vendor?.citations?.[0]?.blockId).toBe("b2");
    expect(invoice.fields?.total?.value).toBe(1200);
    expect(invoice.fields?.total?.citations?.[0]?.blockId).toBe("t1");
    const nested = reductoToDocument(load("extract/nested"), SOURCE, "r-1");
    expect(nested.fields?.["bill_to.name"]?.citations?.[0]?.bbox).toBeUndefined();
    expect(nested.fields?.["bill_to.name"]?.citations?.[0]?.blockId).toBe("b1");
    expect(nested.fields?.["items.0.qty"]?.value).toBe(2);
    expect(nested.fields?.paid?.value).toBe(false);
    expect(nested.fields?.notes?.value).toBe('{"extra":true}');
  });

  it("prices pages without treating credits as dollars", () => {
    expect(reductoCostUsd("r-1", 1, false)).toBe(0.01);
    expect(reductoCostUsd("legacy", 2, false)).toBe(0.03);
    expect(reductoCostUsd("r-1", 1, true)).toBe(0.03);
    expect(reductoCostUsd("custom", 4, false)).toBe(0);
    expect(reductoCostUsd("custom", 4, true)).toBe(0.08);
    expect(reductoCostUsd("r-1", 0, true)).toBe(0);
  });

  it("requires the API key and omits it from the cache identity", async () => {
    delete process.env.REDUCTO_API_KEY;
    const adapter = new ReductoAdapter(null, { model: " legacy ", timeout_s: 5, api_key: "nope" });
    await expect(adapter.available()).resolves.toEqual({ ok: false, reason: API_KEY });
    process.env.REDUCTO_API_KEY = "  token  ";
    await expect(adapter.available()).resolves.toEqual({ ok: true });
    const identity = adapter.cacheIdentity();
    expect(identity.model).toBe("legacy");
    expect(JSON.stringify(identity)).not.toContain("token");
    expect(identity.options).toEqual({});
    expect(new ReductoAdapter(null, { settings: { model: "r-1" }, extractSchema: "schema.json" }).cacheIdentity().model).toBe("r-1");
    expect(resolve("reducto").id).toBe("reducto");
  });

  it("uploads, parses, and extracts", async () => {
    process.env.REDUCTO_API_KEY = "secret-key";
    process.env.REDUCTO_BASE_URL = "https://example.test/root///";
    const file = pdf();
    writeFileSync(path.join(path.dirname(file), "invoice.schema.json"), '{"type":"object"}');
    const calls = script([
      [200, { file_id: " reducto://file " }],
      [200, { job_id: "job-1", result: { type: "full", chunks: [] }, usage: { num_pages: 2, credits: 9 } }],
      [200, { job_id: "ex-1", result: { vendor: { value: "Acme", citations: [] } } }],
    ]);
    const raw = await new ReductoAdapter(null, {
      model: "r-1",
      extract_schema: "invoice.schema.json",
      page_size: [612, 792],
      retrieval: { chunking: { chunk_mode: "page" } },
      timeout_s: 30,
    }).fetch(file);
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toBe("https://example.test/root/upload");
    expect(calls[0]?.headers.Authorization).toBe("Bearer secret-key");
    expect(calls[0]?.form?.get("file")).toBeInstanceOf(Blob);
    const body = JSON.parse(calls[1]?.body ?? "{}") as { input: string; settings: { model: string }; retrieval: { chunking: { chunk_mode: string } } };
    expect(body.input).toBe("reducto://file");
    expect(body.settings.model).toBe("r-1");
    expect(body.retrieval.chunking.chunk_mode).toBe("page");
    const extract = JSON.parse(calls[2]?.body ?? "{}") as { input: string; settings: { citations: { enabled: boolean } } };
    expect(extract.input).toBe("jobid://job-1");
    expect(extract.settings.citations.enabled).toBe(true);
    expect(raw.parserVersion).toBe("r-1");
    expect(raw.costUsd).toBe(0.06);
    expect((raw.payload as { pages: unknown[] }).pages).toEqual([{ width: 612, height: 792 }]);
  });

  it("polls, follows a result URL, and times out", async () => {
    process.env.REDUCTO_API_KEY = "secret-key";
    delete process.env.REDUCTO_BASE_URL;
    const calls = script([
      [200, { file_id: "reducto://f" }],
      [200, { job_id: "job 1", status: "Pending" }],
      [200, { job_id: "job 1", status: "Running" }],
      [200, { job_id: "job 1", status: "In Progress", result: { type: "url", url: "https://files.test/chunks" } }],
      [200, [{ content: "remote", blocks: [{ type: "Text", content: "Hi" }] }]],
    ]);
    const clock = useClock();
    const raw = await new ReductoAdapter(null, { timeoutS: 9, pageSize: [100, 100] }).fetch(pdf());
    expect(calls[2]?.url).toBe("https://platform.reducto.ai/job/job%201");
    expect(calls[4]?.url).toBe("https://files.test/chunks");
    expect(clock.slept).toEqual([1]);
    expect((raw.payload as { parse: { result: { chunks: Array<{ content: string }> } } }).parse.result.chunks[0]?.content).toBe("remote");
    expect(raw.costUsd).toBe(0.01);

    script([
      [200, { file_id: "reducto://f" }],
      [200, { job_id: "slow", status: "Completing" }],
      [200, { job_id: "slow", status: "Completing" }],
      [200, { job_id: "slow", status: "Completing" }],
    ]);
    const slow = useClock();
    await expect(new ReductoAdapter(null, { timeout_s: 1 }).fetch(pdf())).rejects.toThrow(/timed out after 1s/);
    expect(slow.slept).toEqual([1]);

    script([
      [200, { file_id: "reducto://f" }],
      [200, { job_id: "bad", status: "Failed", error: "boom" }],
    ]);
    await expect(new ReductoAdapter(null, {}).fetch(pdf())).rejects.toThrow(/job bad FAILED/);

    script([[503, "no\npe"]]);
    await expect(new ReductoAdapter(null, {}).fetch(pdf())).rejects.toThrow(/HTTP 503[\s\S]*no pe/);

    globalThis.fetch = () => Promise.reject(new Error("down"));
    await expect(new ReductoAdapter(null, {}).fetch(pdf())).rejects.toThrow(/reducto failed on/);
  });

  it("reports config and payload errors", async () => {
    const file = pdf();
    delete process.env.REDUCTO_API_KEY;
    await expect(new ReductoAdapter("fast", {}).fetch(file)).rejects.toThrow(/no variants/);
    await expect(new ReductoAdapter(null, {}).fetch(path.join(path.dirname(file), "missing.pdf"))).rejects.toThrow(/path not found/);
    await expect(new ReductoAdapter(null, {}).fetch(file)).rejects.toThrow(/REDUCTO_API_KEY/);
    process.env.REDUCTO_API_KEY = "secret-key";
    await expect(new ReductoAdapter(null, { timeout_s: 0 }).fetch(file)).rejects.toThrow(/timeout_s/);
    await expect(new ReductoAdapter(null, { timeout_s: true }).fetch(file)).rejects.toThrow(/timeout_s/);
    await expect(new ReductoAdapter(null, { extract_schema: "   " }).fetch(file)).rejects.toThrow(/extract_schema/);
    await expect(new ReductoAdapter(null, { extract_schema: "missing.schema.json" }).fetch(file)).rejects.toThrow(/Looked in/);
    const bad = path.join(path.dirname(file), "bad.json");
    writeFileSync(bad, "[1]");
    await expect(new ReductoAdapter(null, { extract_schema: bad }).fetch(file)).rejects.toThrow(/JSON object/);
    script([[200, { nope: true }]]);
    await expect(new ReductoAdapter(null, {}).fetch(file)).rejects.toThrow(/file_id/);
    script([
      [200, { file_id: "reducto://f" }],
      [200, []],
    ]);
    await expect(new ReductoAdapter(null, {}).fetch(file)).rejects.toThrow(/non-object/);
    script([
      [200, { file_id: "reducto://f" }],
      [200, "nope"],
    ]);
    await expect(new ReductoAdapter(null, {}).fetch(file)).rejects.toThrow(/non-JSON/);
    expect(() => reductoToDocument({ result: { type: "url", url: "https://x" } }, SOURCE, "r-1")).toThrow(/URL/);
    expect(() => reductoToDocument({ result: { chunks: [] } }, SOURCE, "")).toThrow(/parserVersion/);
    expect(() => reductoToDocument([], SOURCE, "r-1")).toThrow(/JSON object/);
    expect(() => reductoToDocument({ result: {} }, SOURCE, "r-1")).toThrow(/result.chunks/);
  });

  it.skipIf(process.env.LIVE !== "1" || !process.env.REDUCTO_API_KEY)("parses one pdf when LIVE=1", async () => {
    const file = pdf();
    const raw = await new ReductoAdapter(null, { timeout_s: 120 }).fetch(file);
    expect(raw.parserVersion).not.toBe("");
    expect(reductoToDocument(raw.payload, SOURCE, raw.parserVersion).meta.parser).toBe("reducto");
  });
});
