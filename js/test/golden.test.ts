import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { createProgram } from "../src/cli.js";
import {
  GoldenError,
  canonicalGoldenJson,
  formatIssue,
  loadGolden,
  validateCommand,
} from "../src/golden.js";
import { version } from "../src/version.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixtures = path.join(repoRoot, "spec", "fixtures", "golden");
const previousCwd = process.cwd();

afterEach(() => {
  process.chdir(previousCwd);
  process.exitCode = undefined;
});

function messages(load: () => void): string[] {
  try {
    load();
  } catch (err) {
    expect(err).toBeInstanceOf(GoldenError);
    return (err as GoldenError).issues.map((issue) => formatIssue(issue));
  }
  throw new Error("expected GoldenError");
}

describe("golden loader", () => {
  it("matches the canonical JSON fixture", () => {
    process.chdir(fixtures);
    const files = loadGolden("valid");
    expect(files.map((file) => file.id)).toEqual(["invoice-acme", "partial"]);
    expect(files.map((file) => file.file)).toEqual(["files/doc.pdf", "files/other.pdf"]);
    const fields = files[0]?.expect?.fields;
    expect(fields?.due_date).toBe("2026-11-01");
    expect(fields?.answer).toBe("yes");
    expect(fields?.declined).toBe("no");
    expect(fields?.flag).toBe("on");
    expect(fields?.mode).toBe(15);
    expect(fields?.legacy).toBe(77);
    expect(fields?.note).toBeNull();
    const expected = readFileSync(path.join(fixtures, "expected.json"), "utf8");
    expect(canonicalGoldenJson(files)).toBe(expected);
  });

  it("defaults id to the file stem", () => {
    process.chdir(fixtures);
    const files = loadGolden("valid/partial.yml");
    expect(files[0]?.id).toBe("partial");
    expect(files[0]?.file).toBe("files/other.pdf");
  });

  it("reports every problem with path, line, and column", () => {
    process.chdir(fixtures);
    const expected: Record<string, string[]> = {
      "missing-file.yaml": ["invalid/missing-file.yaml:1:7: file not found: files/no-such.pdf"],
      "bad-col.yaml": ['invalid/bad-col.yaml:8:25: column "Missing" is not in the header'],
      "col-index.yaml": ["invalid/col-index.yaml:8:25: column index 3 is outside the header"],
      "no-header.yaml": ['invalid/no-header.yaml:7:25: column "Price" is not in the header'],
      "same-pair.yaml": [
        "invalid/same-pair.yaml:5:7: reading_order pair must contain two different strings",
      ],
      "schema.yaml": ['invalid/schema.yaml:3:1: unknown property "nope"'],
      "not-a-map.yaml": ["invalid/not-a-map.yaml:1:1: golden file must be a mapping"],
      "dir-file.yaml": ["invalid/dir-file.yaml:1:7: file is not readable: files"],
    };
    for (const [name, wanted] of Object.entries(expected)) {
      expect(messages(() => loadGolden(path.join("invalid", name)))).toEqual(wanted);
    }
    expect(messages(() => loadGolden("invalid/syntax.yaml"))[0]).toContain(
      "invalid/syntax.yaml:2:1: invalid YAML:",
    );
    expect(messages(() => loadGolden("invalid/multi.yaml"))).toEqual([
      "invalid/multi.yaml:1:7: file not found: files/missing.pdf",
      'invalid/multi.yaml:2:1: unknown property "extra"',
      'invalid/multi.yaml:9:25: column "Price" is not in the header',
    ]);
  });

  it("rejects duplicate ids", () => {
    process.chdir(fixtures);
    expect(messages(() => loadGolden("dup"))).toEqual([
      'dup/b.yaml:1:5: duplicate id "same" (also in dup/a.yaml)',
    ]);
  });

  it("uses the validate exit codes", () => {
    process.chdir(fixtures);
    expect(validateCommand("valid")).toBe(0);
    expect(validateCommand("todo")).toBe(0);
    expect(validateCommand("todo", { strict: true })).toBe(1);
    expect(validateCommand("invalid", { strict: true })).toBe(2);
    expect(validateCommand("missing")).toBe(2);
  });

  it("checks schema wording and edge paths", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "golden-"));
    process.chdir(dir);
    mkdirSync(path.join(dir, "files"));
    writeFileSync(path.join(dir, "files", "doc.pdf"), "");
    const cases: Record<string, [string, string[]]> = {
      "missing.yaml": ["id: only\n", ['missing.yaml:1:1: missing property "file"']],
      "empty.yaml": ['file: ""\n', ["empty.yaml:1:7: must not be empty"]],
      "type.yaml": ["file: 1\n", ["type.yaml:1:7: must be string"]],
      "page.yaml": [
        "file: files/doc.pdf\nexpect:\n  tables:\n    - page: 0\n",
        ["page.yaml:4:13: must be >= 1"],
      ],
      "items.yaml": [
        "file: files/doc.pdf\nexpect:\n  reading_order:\n    - [A, B, C]\n",
        ["items.yaml:4:7: must have at most 2 items"],
      ],
      "short.yaml": [
        "file: files/doc.pdf\nexpect:\n  reading_order:\n    - [only]\n",
        ["short.yaml:4:7: must have at least 2 items"],
      ],
      "col.yaml": [
        "file: files/doc.pdf\nexpect:\n  tables:\n    - page: 1\n      cells:\n        - {row: 0, col: true, value: x}\n",
        ["col.yaml:6:25: must be a column index or a header name"],
      ],
      "dupkey.yaml": [
        "file: files/doc.pdf\nid: one\nid: two\n",
        ['dupkey.yaml:3:1: duplicate key "id"'],
      ],
      "field.yaml": [
        "file: files/doc.pdf\nexpect:\n  fields:\n    total: {bad: 1}\n",
        ["field.yaml:4:12: must be string, number, boolean, or null"],
      ],
    };
    for (const [name, [text, wanted]] of Object.entries(cases)) {
      writeFileSync(path.join(dir, name), text);
      expect(messages(() => loadGolden(name))).toEqual(wanted);
    }
    writeFileSync(path.join(dir, "bad.yaml"), Buffer.from([0x66, 0x69, 0x6c, 0x65, 0x3a, 0x20, 0xff, 0x0a]));
    expect(messages(() => loadGolden("bad.yaml"))).toEqual(["bad.yaml:1:1: file is not valid UTF-8"]);
    writeFileSync(path.join(dir, "note.txt"), "hi\n");
    expect(messages(() => loadGolden("note.txt"))).toEqual([
      "note.txt:1:1: expected a .yaml or .yml file",
    ]);
    const empty = path.join(dir, "empty");
    mkdirSync(empty);
    expect(loadGolden(empty)).toEqual([]);
    expect(validateCommand(empty)).toBe(0);
    writeFileSync(
      path.join(dir, "scalars.yaml"),
      "file: files/doc.pdf\nexpect:\n  fields:\n    signed: -3\n    plus: +4\n    hex: 0x10\n    nan: .nan\n    ninf: -.inf\n    pinf: +.inf\n",
    );
    const scalars = loadGolden("scalars.yaml")[0]?.expect?.fields;
    expect(scalars?.signed).toBe(-3);
    expect(scalars?.plus).toBe(4);
    expect(scalars?.hex).toBe(16);
    expect(Number.isNaN(scalars?.nan)).toBe(true);
    expect(scalars?.ninf).toBe(Number.NEGATIVE_INFINITY);
    expect(scalars?.pinf).toBe(Number.POSITIVE_INFINITY);
    writeFileSync(path.join(dir, "blank.yaml"), "");
    expect(messages(() => loadGolden("blank.yaml"))).toEqual([
      "blank.yaml:1:1: golden file must be a mapping",
    ]);
    writeFileSync(path.join(dir, "crlf.yaml"), "file: files/doc.pdf\r\n# TODO verify\r\n");
    expect(validateCommand("crlf.yaml", { strict: true })).toBe(1);
  });

  it("wires the validate command", () => {
    process.chdir(fixtures);
    const program = createProgram();
    program.exitOverride();
    expect(() => program.parse(["node", "docassay", "--version"])).toThrow(
      expect.objectContaining({ exitCode: 0 }),
    );
    expect(version).toBe(readFileSync(path.join(repoRoot, "VERSION"), "utf8").trim());
    program.parse(["node", "docassay", "validate", "--strict", "todo"]);
    expect(process.exitCode).toBe(1);
  });
});
