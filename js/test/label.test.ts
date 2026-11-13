import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { createProgram } from "../src/cli.js";
import { validateCommand } from "../src/golden.js";
import {
  LabelError,
  draftYaml,
  labelCommand,
  renderYaml,
  reviewHtml,
  startReview,
} from "../src/label.js";
import type { Document } from "../src/models/document.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixtureDir = path.join(repoRoot, "spec", "fixtures", "label");
const sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const previousCwd = process.cwd();
const config = `version: 1
golden: golden
parsers:
  - id: file
    options: {dir: parsed}
`;

afterEach(() => {
  process.chdir(previousCwd);
  process.exitCode = undefined;
  vi.restoreAllMocks();
});

function fixture(): Document {
  return JSON.parse(readFileSync(path.join(fixtureDir, "input.json"), "utf8")) as Document;
}

function document(overrides: Partial<Document> = {}): Document {
  return {
    specVersion: "1.0.0",
    source: { path: "files/lease.pdf", sha256: sha, mime: "application/pdf" },
    meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
    pages: [{ number: 1 }],
    blocks: [],
    tables: [],
    ...overrides,
  };
}

function block(
  id: string,
  text: string,
  page = 1,
  type: Document["blocks"][number]["type"] = "paragraph",
): Document["blocks"][number] {
  return { id, type, text, page };
}

function prepare(root: string): void {
  mkdirSync(path.join(root, "files"), { recursive: true });
  mkdirSync(path.join(root, "parsed"), { recursive: true });
  writeFileSync(path.join(root, "files", "lease.pdf"), Buffer.from("%PDF-1.4\n"));
  writeFileSync(
    path.join(root, "parsed", "lease.json"),
    readFileSync(path.join(fixtureDir, "input.json")),
  );
  writeFileSync(path.join(root, "docassay.yaml"), config);
}

describe("label", () => {
  it("matches the fixture and stays stable", () => {
    const drafted = draftYaml(fixture(), "../files/lease.pdf", "lease");
    expect(drafted).toBe(readFileSync(path.join(fixtureDir, "expected.yaml"), "utf8"));
    expect(draftYaml(fixture(), "../files/lease.pdf", "lease")).toBe(drafted);
  });

  it("applies snippet, table, and reading-order rules", () => {
    const longWord = "z".repeat(90);
    const cut = draftYaml(
      document({
        blocks: [block("b1", `short ${"y".repeat(90)}`, 1, "footnote"), block("b2", longWord)],
      }),
      "a.pdf",
      "a",
    );
    expect(cut).toContain(`"${longWord.slice(0, 80)}"`);
    expect(cut).toContain("short");
    expect(cut).not.toContain("y".repeat(10));

    const kept = draftYaml(
      document({
        tables: [
          { id: "empty", page: 1, rows: [] },
          { id: "small", page: 1, header: ["S"], rows: [["1"]] },
          { id: "mid", page: 1, header: ["M", "N"], rows: [["1", "2"], ["3", "4"]] },
          { id: "big", page: 1, header: ["A", "B", "C"], rows: [["1", "2", "3"], ["1", "2", "3"], ["1", "2", "3"]] },
          {
            id: "huge",
            page: 1,
            header: ["W", "X", "Y", "Z"],
            rows: [["1", "2", "3", "4"], ["1", "2", "3", "4"], ["1", "2", "3", "4"], ["1", "2", "3", "4"]],
          },
        ],
      }),
      "a.pdf",
      "a",
    );
    expect(kept.indexOf('"W"')).toBeLessThan(kept.indexOf('"A"'));
    expect(kept.indexOf('"A"')).toBeLessThan(kept.indexOf('"M"'));
    expect(kept).not.toContain('"S"');

    const bare = draftYaml(
      document({ tables: [{ id: "h", page: 1, header: ["Only"], rows: [] }] }),
      "a.pdf",
      "a",
    );
    expect(bare).not.toContain("cells:");
    expect(bare).toContain("rows: 0 # TODO verify");

    const dup = draftYaml(
      document({
        tables: [{ id: "d", page: 1, header: ["A", "A", ""], rows: [["l", "r", "e"]] }],
      }),
      "a.pdf",
      "a",
    );
    expect(dup).toContain("col: 0 # TODO verify");
    expect(dup).toContain("col: 1 # TODO verify");
    expect(dup).not.toContain('col: "A"');

    const single = draftYaml(
      document({ blocks: [block("b1", "one"), block("b2", "two"), block("b3", "three")] }),
      "a.pdf",
      "a",
    );
    expect(single).toContain('- - "one" # TODO verify\n      - "three" # TODO verify');
    expect(single).toContain('- - "one" # TODO verify\n      - "two" # TODO verify');

    const spanned = draftYaml(
      document({
        pages: [{ number: 1 }, { number: 2 }, { number: 3 }],
        blocks: [
          block("b1", "End one", 1),
          block("b2", "Start two", 2),
          block("b3", "End two", 2),
          block("b4", "Start three", 3),
          block("b5", "End three", 3),
        ],
      }),
      "a.pdf",
      "a",
    );
    expect(spanned).toContain('- - "End one" # TODO verify\n      - "Start two" # TODO verify');
    expect(spanned).toContain('- - "End two" # TODO verify\n      - "Start three" # TODO verify');

    const repeated = draftYaml(
      document({ blocks: [block("b1", "Same."), block("b2", "Same.")] }),
      "a.pdf",
      "a",
    );
    expect(repeated).not.toContain("reading_order:");
    expect(repeated.split('"Same."').length - 1).toBe(1);
  });

  it("quotes strings and unusual field keys", () => {
    const drafted = draftYaml(
      document({
        fields: {
          say: { value: 'He said "hi"\nnext' },
          "a-b": { value: "x" },
          café: { value: "oui" },
          rate: { value: 1.5 },
          ok: { value: true },
        },
      }),
      "a.pdf",
      "a",
    );
    expect(drafted).toContain('say: "He said \\"hi\\"\\nnext" # TODO verify');
    expect(drafted).toContain('"a-b": "x" # TODO verify');
    expect(drafted).toContain('"café": "oui" # TODO verify');
    expect(drafted).toContain("rate: 1.5 # TODO verify");
    expect(drafted).toContain("ok: true # TODO verify");
    expect(renderYaml({ file: "a.pdf" }, false)).toBe('file: "a.pdf"\n');
    expect(() => renderYaml({ file: "" }, false)).toThrow(LabelError);
  });

  it("fails strict validate until the TODO marks are removed", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    mkdirSync(path.join(root, "golden"));
    mkdirSync(path.join(root, "files"));
    writeFileSync(path.join(root, "files", "lease.pdf"), Buffer.from("%PDF-1.4\n"));
    const target = path.join(root, "golden", "lease.yaml");
    writeFileSync(target, readFileSync(path.join(fixtureDir, "expected.yaml")));
    process.chdir(root);
    expect(validateCommand(target, { strict: true })).toBe(1);
    const drafted = draftYaml(fixture(), "../files/lease.pdf", "lease");
    const confirmed = drafted
      .split("\n")
      .map((line) => line.replace(" # TODO verify", ""))
      .join("\n");
    writeFileSync(target, confirmed);
    expect(readFileSync(target, "utf8")).not.toContain("# TODO verify");
    expect(validateCommand(target, { strict: true })).toBe(0);
  });

  it("serves the review page on localhost and saves without TODOs", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    mkdirSync(path.join(root, "golden"));
    mkdirSync(path.join(root, "files"));
    const pdf = Buffer.from("%PDF-1.4\n");
    writeFileSync(path.join(root, "files", "lease.pdf"), pdf);
    const target = path.join(root, "golden", "lease.yaml");
    writeFileSync(target, readFileSync(path.join(fixtureDir, "expected.yaml")));
    const server = await startReview(target, false);
    try {
      const page = await (await fetch(`${server.url}/`)).text();
      expect(page).toContain("Widget");
      expect(page).toContain('iframe src="/source"');
      expect(page).toContain('fetch("/save"');
      const lowered = page.toLowerCase();
      expect(lowered).not.toContain("cdnjs");
      expect(lowered).not.toContain("unpkg");
      expect(lowered).not.toContain("jsdelivr");
      expect((await fetch(`${server.url}/missing`)).status).toBe(404);
      const source = await fetch(`${server.url}/source`);
      expect(source.headers.get("content-type")).toBe("application/pdf");
      expect(Buffer.from(await source.arrayBuffer())).toEqual(pdf);
      const rejected = await fetch(`${server.url}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: '{"nope": 1}',
      });
      expect(rejected.status).toBe(400);
      const saved = await fetch(`${server.url}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file: "../files/lease.pdf",
          id: "lease",
          expect: { text_contains: ["Kept sentence."] },
        }),
      });
      expect(await saved.text()).toBe("saved\n");
      await server.done;
      const text = readFileSync(target, "utf8");
      expect(text).not.toContain("# TODO verify");
      expect(text).toContain("Kept sentence.");
    } finally {
      await server.close();
    }
  });

  it("rejects a bad golden file and a missing source", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    await expect(startReview(path.join(root, "absent.yaml"), false)).rejects.toBeInstanceOf(LabelError);
    writeFileSync(path.join(root, "broken.yaml"), "[\n");
    await expect(startReview(path.join(root, "broken.yaml"), false)).rejects.toBeInstanceOf(LabelError);
    writeFileSync(path.join(root, "list.yaml"), "[]\n");
    await expect(startReview(path.join(root, "list.yaml"), false)).rejects.toBeInstanceOf(LabelError);
    const plain = path.join(root, "plain.yaml");
    writeFileSync(plain, 'file: "missing.pdf"\nid: "plain"\n');
    const server = await startReview(plain, false);
    try {
      expect((await fetch(`${server.url}/source`)).status).toBe(404);
      expect(await (await fetch(`${server.url}/shutdown`)).text()).toBe("bye\n");
      await server.done;
    } finally {
      await server.close();
    }
    expect(reviewHtml({ file: "a.pdf", id: "a" }).toLowerCase()).toContain("<html");
  });

  it("drafts through the file adapter and reports usage errors", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    prepare(root);
    process.chdir(root);
    const stdout: string[] = [];
    const stderr: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
      stdout.push(String(chunk));
      return true;
    });
    vi.spyOn(process.stderr, "write").mockImplementation((chunk) => {
      stderr.push(String(chunk));
      return true;
    });
    expect(await labelCommand("files/lease.pdf", { parser: "file", config: "docassay.yaml" })).toBe(0);
    expect(readFileSync(path.join(root, "golden", "lease.yaml"), "utf8")).toBe(
      readFileSync(path.join(fixtureDir, "expected.yaml"), "utf8"),
    );
    expect(stdout.join("")).toContain("wrote golden/lease.yaml\n");
    expect(
      await labelCommand("files/lease.pdf", {
        parser: "file",
        out: "nested/out.yaml",
        config: "docassay.yaml",
      }),
    ).toBe(0);
    expect(readFileSync(path.join(root, "nested", "out.yaml"), "utf8").startsWith('file: "../files/lease.pdf"\n')).toBe(
      true,
    );
    expect(await labelCommand("missing.pdf", { parser: "file" })).toBe(2);
    expect(await labelCommand("files/lease.pdf", { parser: "file", review: true })).toBe(2);
    expect(await labelCommand("files/lease.pdf")).toBe(2);
    expect(await labelCommand("files/lease.pdf", { parser: "no-such-parser" })).toBe(2);
    writeFileSync(path.join(root, "parsed", "lease.json"), "");
    expect(await labelCommand("files/lease.pdf", { parser: "file", config: "docassay.yaml" })).toBe(3);
    writeFileSync(path.join(root, "docassay.yaml"), "version: 1\ngolden: golden\nparsers: [\n");
    expect(await labelCommand("files/lease.pdf", { parser: "file", config: "docassay.yaml" })).toBe(2);
    expect(await labelCommand("missing.yaml", { review: true, openBrowser: false })).toBe(2);
    expect(stderr.join("")).toContain("file not found");
  });

  it("prints the review url from the command", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    const target = path.join(root, "plain.yaml");
    writeFileSync(target, 'file: "missing.pdf"\nid: "plain"\n');
    const stdout: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
      stdout.push(String(chunk));
      return true;
    });
    const pending = labelCommand(target, { review: true, openBrowser: false });
    let url = "";
    const deadline = Date.now() + 2000;
    while (url === "" && Date.now() < deadline) {
      const match = stdout.join("").match(/review at (http:\/\/127\.0\.0\.1:\d+)/);
      url = match?.[1] ?? "";
      if (url === "") await new Promise((resolve) => setTimeout(resolve, 10));
    }
    expect(await (await fetch(`${url}/shutdown`)).text()).toBe("bye\n");
    expect(await pending).toBe(0);
    expect(stdout.join("")).toContain(`wrote ${target.split(path.sep).join("/")}\n`);
  });

  it("wires the label command", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-label-"));
    prepare(root);
    process.chdir(root);
    await createProgram().parseAsync(["label", "files/lease.pdf"], { from: "user" });
    expect(process.exitCode).toBe(2);
    process.exitCode = undefined;
    await createProgram().parseAsync(
      ["label", "files/lease.pdf", "--from", "file", "-c", "docassay.yaml"],
      { from: "user" },
    );
    expect(process.exitCode).toBeUndefined();
    expect(readFileSync(path.join(root, "golden", "lease.yaml"), "utf8")).toContain('id: "lease"');
  });
});
