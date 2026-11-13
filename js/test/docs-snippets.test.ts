import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { createEcho } from "./docs-snippets/echo-adapter.js";
import { differentText, sameDate, sameNumber } from "./docs-snippets/values.js";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

describe("docs snippets", () => {
  it("matches the published comparison examples", () => {
    expect(sameNumber).toBe(true);
    expect(sameDate).toBe(true);
    expect(differentText).toBe(false);
  });

  it("maps a text file to one paragraph", async () => {
    const dir = mkdtempSync(path.join(tmpdir(), "docassay-echo-"));
    try {
      const file = path.join(dir, "note.txt");
      writeFileSync(file, "hello\n");
      const adapter = createEcho(null, {});
      const raw = await adapter.fetch(file);
      const document = adapter.toDocument(raw, {
        path: "note.txt",
        sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        mime: "text/plain",
      });
      expect(document.blocks[0]?.text).toBe("hello\n");
      await expect(adapter.available()).resolves.toEqual({ ok: true });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("is copied into the adapter and metrics guides", () => {
    const guides = readFileSync(path.join(repo, "docs", "docs", "guides", "adapter.mdx"), "utf8");
    const metrics = readFileSync(path.join(repo, "docs", "docs", "guides", "metrics.mdx"), "utf8");
    const echo = readFileSync(path.join(repo, "js", "test", "docs-snippets", "echo-adapter.ts"), "utf8");
    const values = readFileSync(path.join(repo, "js", "test", "docs-snippets", "values.ts"), "utf8");
    expect(guides).toContain(echo);
    expect(metrics).toContain(values);
  });
});
