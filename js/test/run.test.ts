import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020 } from "ajv/dist/2020.js";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Adapter, RawResult } from "../src/adapters/base.js";
import * as adapterRegistry from "../src/adapters/registry.js";
import { createProgram } from "../src/cli.js";
import { loadConfig } from "../src/config.js";
import type { Document, Source } from "../src/models/document.js";
import { renderJson } from "../src/report/json.js";
import { execute, run } from "../src/runner.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const configPath = path.join(repoRoot, "golden", "docassay.yaml");
const fixture = JSON.parse(readFileSync(path.join(repoRoot, "spec", "fixtures", "run", "example.json"), "utf8")) as {
  runId: string;
  createdAt: string;
};
const schema = JSON.parse(readFileSync(path.join(repoRoot, "spec", "scorecard.schema.json"), "utf8")) as object;
const when = new Date("2026-11-01T18:00:00Z");

const fetches = { count: 0 };

function countingAdapter(): Adapter & { cacheable: boolean } {
  return {
    id: "file",
    variants: [],
    cacheable: true,
    available: () => Promise.resolve({ ok: true, reason: "" }),
    cacheIdentity: () => ({ dir: "counting" }),
    fetch: () => {
      fetches.count += 1;
      const raw: RawResult = { payload: {}, parserVersion: "counting-1", latencyMs: 1500, costUsd: 0.02 };
      return Promise.resolve(raw);
    },
    toDocument: (_raw: RawResult, source: Source): Document => ({
      specVersion: "1.0.0",
      source,
      meta: { parser: "placeholder", parserVersion: "from-adapter", adapterVersion: "0" },
      pages: [{ number: 1 }, { number: 2 }],
      blocks: [{ id: "b1", type: "paragraph", text: "Hello", page: 1 }],
      tables: [],
    }),
  };
}

function withoutClock(payload: { runId?: string; createdAt?: string }): Record<string, unknown> {
  const copy = { ...payload } as Record<string, unknown>;
  delete copy.runId;
  delete copy.createdAt;
  return copy;
}

async function capture(args: string[]): Promise<{ code: number | undefined; stdout: string }> {
  const chunks: string[] = [];
  const write = process.stdout.write.bind(process.stdout);
  process.stdout.write = ((chunk: string | Uint8Array) => {
    chunks.push(typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8"));
    return true;
  }) as typeof process.stdout.write;
  const previous = process.exitCode;
  process.exitCode = undefined;
  try {
    await createProgram().parseAsync(args, { from: "user" });
    return { code: process.exitCode, stdout: chunks.join("") };
  } finally {
    process.stdout.write = write;
    process.exitCode = previous;
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  fetches.count = 0;
});

describe("run", () => {
  it("matches the example fixture and the scorecard schema", async () => {
    const card = await run(loadConfig(configPath), {
      configDir: path.dirname(configPath),
      runId: "run_example",
      createdAt: when,
    });
    const payload = JSON.parse(renderJson(card)) as typeof fixture;
    expect(payload).toEqual(fixture);
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(schema);
    expect(validate(payload)).toBe(true);
  });

  it("serves the cli formats and exit codes", async () => {
    const jsonRun = await capture(["run", "-c", configPath, "--format", "json"]);
    expect(jsonRun.code).toBeUndefined();
    expect(withoutClock(JSON.parse(jsonRun.stdout) as typeof fixture)).toEqual(withoutClock(fixture));
    const markdown = await capture(["run", "-c", configPath, "--format", "md", "-v"]);
    expect(markdown.stdout.startsWith("<!-- docassay-report -->")).toBe(true);
    expect(markdown.stdout).toContain("text not found");
    const root = mkdtempSync(path.join(tmpdir(), "docassay-run-"));
    const htmlPath = path.join(root, "report.html");
    const html = await capture(["run", "-c", configPath, "--format", "html", "-o", htmlPath]);
    expect(html.code).toBeUndefined();
    const page = readFileSync(htmlPath, "utf8");
    expect(page).not.toContain("http");
    expect(page).toContain('id="metric"');
    expect((await capture(["run", "-c", configPath, "--format", "xml"])).code).toBe(2);
    expect((await capture(["run", "-c", configPath, "--parser", "nope"])).code).toBe(2);
    expect((await capture(["run", "-c", configPath, "--tag", "missing"])).code).toBe(2);
    expect((await capture(["run", "-c", configPath, "--concurrency", "0"])).code).toBe(2);
    expect((await capture(["run", "-c", path.join(root, "missing.yaml")])).code).toBe(2);
    const tagged = await capture(["run", "-c", configPath, "--tag", "invoice", "--format", "json"]);
    const taggedPayload = JSON.parse(tagged.stdout) as {
      parsers: Array<{ documents: Array<{ id: string }> }>;
    };
    expect(taggedPayload.parsers[0]?.documents.map((document) => document.id)).toEqual(["invoice-acme"]);
  });

  it("records an adapter error as zero and continues", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-err-"));
    mkdirSync(path.join(root, "files"));
    mkdirSync(path.join(root, "parsed"));
    mkdirSync(path.join(root, "golden"));
    writeFileSync(path.join(root, "files", "ok.pdf"), "%PDF-1.4\n");
    writeFileSync(path.join(root, "files", "gone.pdf"), "%PDF-1.4\n");
    writeFileSync(path.join(root, "parsed", "ok.md"), "# Note\n\nHello\n");
    writeFileSync(
      path.join(root, "golden", "ok.yaml"),
      "file: ../files/ok.pdf\nexpect:\n  text_contains: [Hello]\n  chunks:\n    max_tokens: 10\n",
    );
    writeFileSync(
      path.join(root, "golden", "gone.yaml"),
      "file: ../files/gone.pdf\nexpect:\n  text_contains: [Hello]\n  tables:\n    - {page: 1}\n",
    );
    const config = path.join(root, "docassay.yaml");
    writeFileSync(
      config,
      "version: 1\ngolden: golden\ncache: .docassay/cache\nparsers:\n  - id: file\n    options: {dir: parsed}\n",
    );
    const mixed = await capture(["run", "-c", config, "--format", "json"]);
    expect(mixed.code).toBeUndefined();
    const parsed = JSON.parse(mixed.stdout) as {
      parsers: Array<{
        documents: Array<{
          id: string;
          error?: string;
          scores: Record<string, number>;
          failures: Array<{ reason: string }>;
        }>;
      }>;
    };
    const first = parsed.parsers[0];
    if (first === undefined) {
      throw new Error("expected a parser result");
    }
    const documents = Object.fromEntries(first.documents.map((document) => [document.id, document]));
    expect(documents.ok?.scores.text).toBe(1);
    expect(documents.ok?.scores.chunks).toBe(0);
    expect(documents.ok?.failures[0]?.reason).toBe("parser produced no chunks");
    expect(documents.gone?.error?.startsWith("no output for gone")).toBe(true);
    expect(documents.gone?.scores).toEqual({ tables: 0, text: 0, overall: 0 });
    expect(documents.gone?.failures).toEqual([]);
    mkdirSync(path.join(root, "only"));
    writeFileSync(path.join(root, "only", "gone.yaml"), "file: ../files/gone.pdf\nexpect:\n  text_contains: [Hello]\n");
    writeFileSync(config, "version: 1\ngolden: only\nparsers:\n  - id: file\n    options: {dir: parsed}\n");
    const failed = await capture(["run", "-c", config, "--format", "json"]);
    expect(failed.code).toBe(3);
    expect(failed.stdout).toContain("no output for gone");
  });

  it("exits 3 when a parser is unavailable and 2 when the golden set is empty", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-unavail-"));
    mkdirSync(path.join(root, "golden"));
    writeFileSync(path.join(root, "note.pdf"), "%PDF");
    writeFileSync(path.join(root, "golden", "note.yaml"), "file: ../note.pdf\nexpect:\n  text_contains: [Hi]\n");
    const config = path.join(root, "docassay.yaml");
    writeFileSync(config, "version: 1\ngolden: golden\nparsers:\n  - id: docling\n");
    const unavailable = await capture(["run", "-c", config]);
    expect(unavailable.code).toBe(3);
    expect(unavailable.stdout).toContain("docling");
    mkdirSync(path.join(root, "empty"));
    writeFileSync(config, "version: 1\ngolden: empty\nparsers:\n  - id: file\n    options: {dir: parsed}\n");
    expect((await capture(["run", "-c", config])).code).toBe(2);
  });

  it("reuses a cacheable adapter until refresh or no-cache", async () => {
    vi.spyOn(adapterRegistry, "resolve").mockImplementation(() => countingAdapter());
    const root = mkdtempSync(path.join(tmpdir(), "docassay-cache-"));
    mkdirSync(path.join(root, "files"));
    mkdirSync(path.join(root, "golden"));
    writeFileSync(path.join(root, "files", "note.pdf"), "hello");
    writeFileSync(path.join(root, "golden", "note.yaml"), "file: ../files/note.pdf\nexpect:\n  text_contains: [Hello]\n");
    const configFile = path.join(root, "docassay.yaml");
    writeFileSync(configFile, "version: 1\ngolden: golden\ncache: cache\nparsers:\n  - id: file\n");
    const config = loadConfig(configFile);
    const first = await execute(config, { configDir: root, runId: "run_cache", createdAt: when });
    expect(fetches.count).toBe(1);
    expect(first.card.parsers[0]?.parserVersion).toBe("counting-1");
    expect(first.pages).toEqual([2]);
    const stderr: string[] = [];
    const writeErr = process.stderr.write.bind(process.stderr);
    process.stderr.write = ((chunk: string | Uint8Array) => {
      stderr.push(typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8"));
      return true;
    }) as typeof process.stderr.write;
    try {
      await execute(config, { configDir: root, verbose: true, runId: "run_cache", createdAt: when });
    } finally {
      process.stderr.write = writeErr;
    }
    expect(fetches.count).toBe(1);
    expect(stderr.join("")).toContain("cache hit file");
    await execute(config, { configDir: root, cachePolicy: "refresh" });
    expect(fetches.count).toBe(2);
    await execute(config, { configDir: root, cachePolicy: "no-cache" });
    expect(fetches.count).toBe(3);
    const same = await execute(config, { configDir: root, concurrency: 2, runId: "run_cache", createdAt: when });
    expect(renderJson(same.card)).toBe(renderJson(first.card));
  });
});
