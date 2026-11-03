import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  API_KEY,
  LlamaParseAdapter,
  llamaparseCostUsd,
  llamaparseToDocument,
  pollControl,
} from "../../src/adapters/llamaparse.js";
import { resolve } from "../../src/adapters/registry.js";
import type { Document } from "../../src/models/document.js";
import { version } from "../../src/version.js";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../spec/fixtures/adapters/llamaparse",
);
const SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const SOURCE = { path: "files/sample.pdf", sha256: SHA, mime: "application/pdf" };
const CASES: Array<[string, string, string]> = [
  ["agentic/invoice", "agentic", "2026-09-29"],
  ["fast/edges", "fast", "2026-06-15"],
];

const savedKey = process.env.LLAMA_CLOUD_API_KEY;
const savedBase = process.env.LLAMA_CLOUD_BASE_URL;
const savedFetch = globalThis.fetch;
const savedNow = pollControl.now;
const savedSleep = pollControl.sleep;
const temps: string[] = [];

afterEach(() => {
  restoreKey(savedKey);
  restoreBase(savedBase);
  globalThis.fetch = savedFetch;
  pollControl.now = savedNow;
  pollControl.sleep = savedSleep;
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function restoreKey(value: string | undefined): void {
  if (value === undefined) delete process.env.LLAMA_CLOUD_API_KEY;
  else process.env.LLAMA_CLOUD_API_KEY = value;
}

function restoreBase(value: string | undefined): void {
  if (value === undefined) delete process.env.LLAMA_CLOUD_BASE_URL;
  else process.env.LLAMA_CLOUD_BASE_URL = value;
}

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
  const dir = mkdtempSync(path.join(tmpdir(), "llamaparse-"));
  temps.push(dir);
  const file = path.join(dir, "sample.pdf");
  writeFileSync(file, "%PDF");
  return file;
}

interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  form?: FormData;
}

function script(responses: Array<[number, unknown]>): Call[] {
  const calls: Call[] = [];
  const pending = [...responses];
  globalThis.fetch = async (input, init) => {
    const next = pending.shift();
    if (!next) throw new Error("unexpected fetch");
    const [status, body] = next;
    const headers = headersOf(init?.headers);
    calls.push({
      method: init?.method ?? "GET",
      url: String(input),
      headers,
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
  for (const [key, value] of Object.entries(headers)) {
    out[key] = typeof value === "string" ? value : value.join(",");
  }
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

describe("llamaparse", () => {
  it("matches the recorded documents", () => {
    for (const [name, variant, parserVersion] of CASES) {
      const document = llamaparseToDocument(load(name), SOURCE, parserVersion, variant);
      expect(canonical(document)).toBe(readText(`${name}.doc.json`));
      expect(document.meta.parser).toBe(`llamaparse:${variant}`);
      expect(document.meta.parserVersion).toBe(parserVersion);
      expect(document.meta.adapterVersion).toBe(version);
    }
  });

  it("keeps the invoice header, table, and footer", () => {
    const document = llamaparseToDocument(load("agentic/invoice"), SOURCE, "2026-09-29", "agentic");
    expect(document.blocks.map((block) => block.type)).toEqual([
      "header",
      "heading",
      "paragraph",
      "list_item",
      "list_item",
      "footer",
    ]);
    expect(document.blocks[0]?.bbox).toEqual([72, 36, 192, 48]);
    expect(document.blocks[1]?.level).toBe(1);
    expect(document.tables[0]?.header).toEqual(["Item", "Qty", "Price"]);
    expect(document.tables[0]?.rows[0]).toEqual(["Widget", "2", ""]);
    expect(document.tables[0]?.rows[1]?.[2]).toBe("1,200.00");
    expect(document.tables[0]?.markdown?.startsWith("| Item")).toBe(true);
  });

  it("scales boxes, repeats spans, and keeps the failed page", () => {
    const document = llamaparseToDocument(load("fast/edges"), SOURCE, "2026-06-15", "fast");
    expect(document.blocks[0]?.text).toBe("Running");
    expect(document.blocks[1]?.bbox).toEqual([20, 80, 100, 100]);
    expect(document.blocks[2]?.bbox).toEqual([1, 0, 11, 6]);
    expect(document.blocks[3]?.bbox).toBeUndefined();
    expect(document.tables[0]?.header).toEqual(["H", "Q"]);
    expect(document.tables[0]?.rows).toEqual([["H", "A & B"]]);
    expect(document.blocks.at(-1)?.type).toBe("footer");
    expect(document.blocks.at(-1)?.text).toBe("End");
    expect(document.blocks.at(-1)?.page).toBe(2);
    expect(document.pages.map((page) => page.number)).toEqual([1, 2]);
    expect(document.pages[1]?.width).toBeUndefined();
  });

  it("maps odd items and html-only tables", () => {
    const payload = {
      items: {
        pages: [
          "skip",
          {
            page_number: 2,
            page_width: 0,
            page_height: 10,
            items: [
              {
                type: "table",
                html: "<table><tr><td>a</td><td colspan='2'>b</td></tr><tr><td>&#38;</td><td/><td>c &nope;</td></tr></table>",
              },
              { type: "table", rows: [["a"], ["b", "c"]], md: "" },
              { type: "table", rows: [["a"], "nope"] },
              { type: "header", md: "  Top  " },
              { type: "header", items: [{ type: "heading", level: 3, value: "In" }] },
              { type: "link", text: "site", url: "https://example.com" },
              { type: "heading", level: 2, value: "H2" },
              { type: "list", items: [{ type: "code", value: "z = 1" }] },
              "nope",
              { type: "text", value: "nan", bbox: [{ x: Number.NaN, y: 1, w: 1, h: 1 }] },
              { type: "text", value: "flip", bbox: [{ x: 5, y: 4, w: -3, h: -1 }] },
              { type: "text", value: "unit", bbox: [{ x: 0.2, y: 0.2, w: 0.2, h: 0.2 }] },
              { type: "table", rows: [[1.5, 2, Number.NaN, true, { n: 1 }]] },
              { type: "table", html: "<p>no table</p>" },
              {
                type: "table",
                html: "<table><tr><td xcolspan=9 colspan=2><b>z</b> &ampersandtoolong; & x</td><td>no-close</tr></table>",
              },
              {
                type: "table",
                rows: [["kept"]],
                html: `<tricky</tr>${"<tr><td colspan=\"1\">a</td></tr>".repeat(70)}`,
              },
              { type: 1, md: "misc" },
              {
                type: "header",
                items: [
                  { type: "image", caption: "Cap" },
                  { type: "image", caption: "", md: "pic" },
                  { type: "link", text: "", md: "L" },
                  { type: "formula" },
                ],
              },
              { type: "text", value: "partial", bbox: ["no", { x: 1, y: 2, w: 3, h: 4 }] },
            ],
          },
          { page_number: "2", items: "nope" },
          { page_number: true },
          { page_number: 9 },
        ],
      },
      markdown: { pages: ["no", { page_number: 9, header: "Nine", footer: 1 }] },
    };
    const document = llamaparseToDocument(payload, SOURCE, "2026-09-28", null);
    expect(document.meta.parser).toBe("llamaparse");
    const sized = document.pages.find((page) => page.number === 2);
    expect(sized?.width).toBeUndefined();
    expect(sized?.height).toBe(10);
    expect(document.tables[0]?.header).toEqual(["a", "b", "b"]);
    expect(document.tables[0]?.rows).toEqual([["&", "", "c &nope;"]]);
    expect(document.tables[1]?.header).toEqual(["a", ""]);
    expect(document.tables[1]?.rows).toEqual([["b", "c"]]);
    expect(document.tables.map((table) => table.header)).toContainEqual(["1.5", "2", "", "", ""]);
    expect(document.tables.map((table) => table.header)).toContainEqual(["kept"]);
    const byText = new Map(document.blocks.map((block) => [block.text, block]));
    expect(byText.get("Top")?.type).toBe("header");
    expect(byText.get("In")?.level).toBeUndefined();
    expect(byText.get("site")?.type).toBe("other");
    expect(byText.get("H2")?.level).toBe(2);
    expect(byText.get("z = 1")?.type).toBe("code");
    expect(byText.get("nan")?.bbox).toBeUndefined();
    expect(byText.get("flip")?.bbox).toEqual([2, 3, 5, 4]);
    expect(byText.get("unit")?.bbox).toBeUndefined();
    expect(byText.get("misc")?.type).toBe("other");
    expect(byText.get("Cap")?.type).toBe("header");
    expect(byText.get("L")?.text).toBe("L");
    expect(byText.get("pic")?.type).toBe("header");
    expect(byText.get("partial")?.bbox).toEqual([1, 2, 4, 6]);
    expect(byText.get("Nine")?.type).toBe("header");
  });

  it("rejects a payload that is not a LlamaParse result", () => {
    expect(() => llamaparseToDocument(["nope"], SOURCE, "2026-09-29", "fast")).toThrow(/JSON object/);
    expect(() => llamaparseToDocument({ items: { pages: [] } }, SOURCE, "", "fast")).toThrow(/parserVersion is empty/);
    expect(() => llamaparseToDocument({ items: {} }, SOURCE, "2026-09-29", "fast")).toThrow(/expand=items/);
  });

  it("prices each tier from the credit table", () => {
    expect(llamaparseCostUsd("fast", 2)).toBe((2 * 1.25) / 1000);
    expect(llamaparseCostUsd("cost_effective", 1)).toBe((3 * 1.25) / 1000);
    expect(llamaparseCostUsd("agentic", 1)).toBe((10 * 1.25) / 1000);
    expect(llamaparseCostUsd("agentic_plus", 2)).toBe((90 * 1.25) / 1000);
    expect(llamaparseCostUsd("fast", 0)).toBe(0);
    expect(llamaparseCostUsd("other", 3)).toBe(0);
    expect(llamaparseCostUsd("fast", 1.5)).toBe(0);
  });

  it("requires the API key and omits it from the cache identity", async () => {
    delete process.env.LLAMA_CLOUD_API_KEY;
    const adapter = resolve("llamaparse:agentic");
    expect(adapter).toBeInstanceOf(LlamaParseAdapter);
    await expect(adapter.available()).resolves.toEqual({ ok: false, reason: API_KEY });
    process.env.LLAMA_CLOUD_API_KEY = "  ";
    await expect(adapter.available()).resolves.toEqual({ ok: false, reason: API_KEY });
    process.env.LLAMA_CLOUD_API_KEY = "secret";
    await expect(adapter.available()).resolves.toEqual({ ok: true });
    expect(
      new LlamaParseAdapter("agentic", {
        version: " 2026-09-29 ",
        lang: "en",
        apiKey: "nope",
        timeout_s: 5,
        baseUrl: "http://x",
      }).cacheIdentity(),
    ).toEqual({ variant: "agentic", version: "2026-09-29", options: { lang: "en" } });
  });

  it("polls, prices, and records the version the API reports", async () => {
    process.env.LLAMA_CLOUD_API_KEY = "secret";
    const invoice = load("agentic/invoice");
    const calls = script([
      [200, { id: "pjb 1", status: "PENDING" }],
      [200, { job: { status: " running " } }],
      [200, { job: { status: "RUNNING" } }],
      [200, invoice],
    ]);
    const clock = useClock();
    const adapter = new LlamaParseAdapter("agentic", {
      version: " latest ",
      lang: "en",
      apiKey: "nope",
      timeout_s: 30,
      baseUrl: "https://example.test/",
    });
    const raw = await adapter.fetch(pdf());
    expect(raw.parserVersion).toBe("2026-09-29");
    expect(raw.costUsd).toBe(llamaparseCostUsd("agentic", 1));
    expect(raw.payload).toEqual(invoice);
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toBe("https://example.test/api/v2/parse/upload");
    expect(calls[0]?.headers.Authorization ?? calls[0]?.headers.authorization).toBe("Bearer secret");
    const configuration = calls[0]?.form?.get("configuration");
    expect(JSON.parse(String(configuration))).toEqual({ tier: "agentic", version: "latest", lang: "en" });
    const file = calls[0]?.form?.get("file");
    expect(file instanceof File ? file.name : "").toBe("sample.pdf");
    expect(calls[1]?.url).toBe("https://example.test/api/v2/parse/pjb%201?expand=items,markdown,job_metadata");
    expect(clock.slept).toEqual([1, 2]);
    expect(adapter.toDocument(raw, SOURCE).meta.parser).toBe("llamaparse:agentic");
  });

  it("falls back through version fields and surfaces terminal jobs", async () => {
    process.env.LLAMA_CLOUD_API_KEY = "secret";
    const file = pdf();
    useClock();
    const run = async (body: unknown, parserVersion = "latest") => {
      script([
        [200, { id: "job-1" }],
        [200, body],
      ]);
      return new LlamaParseAdapter("fast", { version: parserVersion, timeout_s: 10 }).fetch(file);
    };
    const pinned = await run({ job: { status: "COMPLETED" }, markdown: { pages: [{}, {}] } });
    expect(pinned.parserVersion).toBe("unknown");
    expect(pinned.costUsd).toBe(llamaparseCostUsd("fast", 2));
    const dated = await run({
      job: { status: "COMPLETED", parameters: { version: " 2026-09-28 " } },
      items: { pages: [] },
    });
    expect(dated.parserVersion).toBe("2026-09-28");
    expect(dated.costUsd).toBe(0);
    const meta = await run({
      job: { status: "COMPLETED" },
      job_metadata: { parameters: { version: "2026-06-15" } },
      items: { pages: [{}] },
    });
    expect(meta.parserVersion).toBe("2026-06-15");
    const rawParams = await run({
      job: { status: "COMPLETED" },
      raw_parameters: { parameters: { version: "2026-09-28" } },
      items: { pages: [{}] },
    });
    expect(rawParams.parserVersion).toBe("2026-09-28");
    const direct = await run({
      job: { status: "COMPLETED" },
      raw_parameters: { version: "2026-06-15" },
      items: { pages: [{}] },
    });
    expect(direct.parserVersion).toBe("2026-06-15");
    const metaVersion = await run({
      job: { status: "COMPLETED" },
      job_metadata: { version: "2026-09-29" },
      items: { pages: [{}] },
    });
    expect(metaVersion.parserVersion).toBe("2026-09-29");
    const requested = await run({ job: { status: "COMPLETED" }, items: { pages: [{}] } }, "2026-01-08");
    expect(requested.parserVersion).toBe("2026-01-08");
    const nested = script([
      [200, { job: { id: "nested" } }],
      [200, { job: { status: "COMPLETED" }, items: { pages: [{}] } }],
    ]);
    expect((await new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).parserVersion).toBe("unknown");
    expect(nested[1]?.url).toContain("nested");
    script([
      [200, { id: "job-1" }],
      [200, { job: { status: "FAILED", error_message: " ocr crashed " } }],
    ]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/FAILED on/);
    script([
      [200, { id: "job-1" }],
      [200, { job: { status: "CANCELLED" } }],
    ]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/conversion failed/);
  });

  it("times out, reports HTTP failures, and checks config", async () => {
    process.env.LLAMA_CLOUD_API_KEY = "secret";
    const file = pdf();
    const clock = useClock();
    script([
      [200, { id: "job-1" }],
      [200, { job: { status: "PENDING" } }],
      [200, { job: { status: "PENDING" } }],
      [200, { job: { status: "PENDING" } }],
    ]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 1.5 }).fetch(file)).rejects.toThrow(/timed out after 1.5s/);
    expect(clock.slept).toEqual([1, 0.5]);
    script([[500, "x".repeat(250)]]);
    await expect(new LlamaParseAdapter("cost_effective", { timeoutS: 3 }).fetch(file)).rejects.toThrow(/x{200}/);
    script([[400, "no\npe"]]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/no pe/);
    script([
      [200, { id: "job-1" }],
      [200, { job: { status: 1 } }],
      [200, { job: { status: "COMPLETED" }, items: { pages: [{}] } }],
    ]);
    expect((await new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).parserVersion).toBe("unknown");
    script([[200, "not-json"]]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/non-JSON/);
    script([[200, [1]]]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/non-object/);
    script([[200, { status: "PENDING" }]]);
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/did not return a job id/);
    globalThis.fetch = () => Promise.reject(new Error("refused"));
    await expect(new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(file)).rejects.toThrow(/refused/);
    await expect(new LlamaParseAdapter("fast", {}).fetch(path.join(tmpdir(), "missing-llamaparse.pdf"))).rejects.toThrow(
      /path not found/,
    );
    await expect(new LlamaParseAdapter(null, {}).fetch(file)).rejects.toThrow(/tier variant/);
    for (const bad of ["no", 0, true, Number.POSITIVE_INFINITY]) {
      await expect(new LlamaParseAdapter("fast", { timeout_s: bad }).fetch(file)).rejects.toThrow(/timeout_s/);
    }
    delete process.env.LLAMA_CLOUD_API_KEY;
    await expect(new LlamaParseAdapter("agentic_plus", {}).fetch(file)).rejects.toThrow(/LLAMA_CLOUD_API_KEY/);
  });

  it("reads the base URL from the environment", async () => {
    process.env.LLAMA_CLOUD_API_KEY = "secret";
    process.env.LLAMA_CLOUD_BASE_URL = "https://eu.example.test/";
    const calls = script([
      [200, { id: "job-1" }],
      [200, { job: { status: "COMPLETED", version: "2026-06-15" }, items: { pages: [{}] } }],
    ]);
    useClock();
    const raw = await new LlamaParseAdapter("agentic_plus", { base_url: "" }).fetch(pdf());
    expect(calls[0]?.url).toBe("https://eu.example.test/api/v2/parse/upload");
    expect(raw.parserVersion).toBe("2026-06-15");
    expect(raw.costUsd).toBe(llamaparseCostUsd("agentic_plus", 1));
    delete process.env.LLAMA_CLOUD_BASE_URL;
    const defaults = script([
      [200, { id: "job-1" }],
      [200, { job: { status: "COMPLETED" }, items: { pages: [{}] } }],
    ]);
    await new LlamaParseAdapter("fast", { timeout_s: 5 }).fetch(pdf());
    expect(defaults[0]?.url.startsWith("https://api.cloud.llamaindex.ai/api/v2/parse/upload")).toBe(true);
  });

  it.skipIf(process.env.LIVE !== "1" || !process.env.LLAMA_CLOUD_API_KEY)("parses one pdf when LIVE=1", async () => {
    const dir = mkdtempSync(path.join(tmpdir(), "llamaparse-live-"));
    temps.push(dir);
    const file = path.join(dir, "tiny.pdf");
    writeFileSync(file, "%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n");
    const raw = await new LlamaParseAdapter("fast", { timeout_s: 120 }).fetch(file);
    expect(raw.parserVersion).toBeTruthy();
    const document = new LlamaParseAdapter("fast", {}).toDocument(raw, SOURCE);
    expect(document.meta.parser).toBe("llamaparse:fast");
  });
});
