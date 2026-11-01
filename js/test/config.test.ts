import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { createProgram } from "../src/cli.js";
import {
  ConfigError,
  canonicalConfigJson,
  initCommand,
  loadConfig,
} from "../src/config.js";

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
const validDir = path.join(repoRoot, "spec/fixtures/config/valid");
const invalidDir = path.join(repoRoot, "spec/fixtures/config/invalid");

interface ExpectedIssue {
  line: number;
  column: number;
  message?: string;
  messagePrefix?: string;
}

describe("config", () => {
  it("matches canonical JSON for valid fixtures", () => {
    const expected = JSON.parse(readFileSync(path.join(validDir, "expected.json"), "utf8")) as Record<
      string,
      unknown
    >;
    for (const [name, payload] of Object.entries(expected)) {
      const text = canonicalConfigJson(loadConfig(path.join(validDir, name)));
      expect(text).toBe(`${JSON.stringify(payload, null, 2)}\n`);
    }
  });

  it("reports every invalid fixture issue", () => {
    const expected = JSON.parse(
      readFileSync(path.join(invalidDir, "expected.json"), "utf8"),
    ) as Record<string, ExpectedIssue[]>;
    for (const [name, wanted] of Object.entries(expected)) {
      let caught: unknown;
      try {
        loadConfig(path.join(invalidDir, name));
      } catch (err) {
        caught = err;
      }
      expect(caught).toBeInstanceOf(ConfigError);
      if (!(caught instanceof ConfigError)) continue;
      expect(caught.exitCode).toBe(2);
      expect(caught.issues).toHaveLength(wanted.length);
      wanted.forEach((want, index) => {
        const issue = caught.issues[index];
        expect(issue).toBeDefined();
        if (issue === undefined) return;
        expect(issue.line).toBe(want.line);
        expect(issue.column).toBe(want.column);
        expect(issue.path.endsWith(name)).toBe(true);
        if (want.message !== undefined) expect(issue.message).toBe(want.message);
        else expect(issue.message.startsWith(want.messagePrefix ?? "")).toBe(true);
        expect(caught.message).toContain(issue.toString());
      });
    }
  });

  it("reports a missing path, a directory, and invalid UTF-8", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-config-"));
    try {
      expect(() => loadConfig(path.join(root, "missing.yaml"))).toThrow(/path not found/);
      expect(() => loadConfig(root)).toThrow(/path is not a file/);
      const bad = path.join(root, "bad.yaml");
      writeFileSync(bad, Buffer.from([0xff]));
      expect(() => loadConfig(bad)).toThrow(/file is not valid UTF-8/);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("resolves absolute paths and keeps YAML 1.2 numbers", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-paths-"));
    try {
      const project = path.join(root, "proj");
      mkdirSync(project);
      const golden = path.join(root, "sets");
      const cache = path.join(root, "cache");
      const baseline = path.join(root, "base.json");
      const parsed = path.join(root, "parsed");
      const config = path.join(project, "docassay.yaml");
      writeFileSync(
        config,
        [
          "version: 1",
          `golden: ${golden}`,
          `cache: ${cache}`,
          `baseline: ${baseline}`,
          "parsers:",
          "  - id: file",
          "    options:",
          `      dir: ${parsed}`,
          "  - id: docling",
          "    options:",
          "      inf: .inf",
          "      nan: .nan",
          "      ninf: -.inf",
          "      dir: 1",
          "",
        ].join("\n"),
      );
      const loaded = loadConfig(config);
      expect(loaded.golden).toBe("../sets");
      expect(loaded.cache).toBe("../cache");
      expect(loaded.baseline).toBe("../base.json");
      expect(loaded.parsers[0]?.options?.dir).toBe("../parsed");
      const options = loaded.parsers[1]?.options;
      expect(options?.inf).toBe(Number.POSITIVE_INFINITY);
      expect(options?.nan).toBeNaN();
      expect(options?.ninf).toBe(Number.NEGATIVE_INFINITY);
      expect(options?.dir).toBe(1);

      const dotted = path.join(root, "here.yaml");
      writeFileSync(dotted, "version: 1\ngolden: .\nparsers:\n  - id: file\n    options: {dir: ''}\n");
      const emptyDir = loadConfig(dotted);
      expect(emptyDir.golden).toBe(".");
      expect(emptyDir.parsers[0]?.options?.dir).toBe("");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("writes starter files and refuses to overwrite them", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-init-"));
    const logs: string[] = [];
    const original = console.log;
    console.log = (...args: unknown[]) => {
      logs.push(args.map((item) => String(item)).join(" "));
    };
    try {
      expect(initCommand(root)).toBe(0);
      const config = readFileSync(path.join(root, "docassay.yaml"), "utf8");
      const golden = readFileSync(path.join(root, "golden", "example.yaml"), "utf8");
      expect(config).toContain("id: file");
      expect(config).toContain("name: my-pipeline");
      expect(golden).toContain("files/example.pdf");
      const loaded = loadConfig(path.join(root, "docassay.yaml"));
      expect(loaded.parsers[0]?.id).toBe("file");
      expect(loaded.parsers[0]?.name).toBe("my-pipeline");
      expect(initCommand(root)).toBe(2);
      expect(logs.some((line) => line.includes("file already exists; rerun with --force"))).toBe(true);
      expect(initCommand(root, true)).toBe(0);
    } finally {
      console.log = original;
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("does not write the other file when one already exists", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-init-one-"));
    try {
      mkdirSync(path.join(root, "golden"));
      writeFileSync(path.join(root, "golden", "example.yaml"), "keep\n");
      expect(initCommand(root)).toBe(2);
      expect(existsSync(path.join(root, "docassay.yaml"))).toBe(false);
      expect(readFileSync(path.join(root, "golden", "example.yaml"), "utf8")).toBe("keep\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("returns 2 when the destination cannot be created", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-init-block-"));
    const blocked = path.join(root, "blocked");
    writeFileSync(blocked, "x");
    try {
      expect(initCommand(blocked)).toBe(2);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("runs init from the command", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-cli-init-"));
    const previous = process.cwd();
    const previousCode = process.exitCode;
    try {
      process.chdir(root);
      const program = createProgram();
      program.exitOverride();
      program.parse(["node", "docassay", "init"]);
      expect(process.exitCode).toBe(0);
      expect(existsSync(path.join(root, "docassay.yaml"))).toBe(true);
      program.parse(["node", "docassay", "init"]);
      expect(process.exitCode).toBe(2);
      program.parse(["node", "docassay", "init", "--force"]);
      expect(process.exitCode).toBe(0);
    } finally {
      process.chdir(previous);
      process.exitCode = previousCode;
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("prints init help", () => {
    const program = createProgram();
    program.exitOverride();
    const logs: string[] = [];
    program.configureOutput({
      writeOut: (text) => logs.push(text),
      writeErr: (text) => logs.push(text),
    });
    expect(() => program.parse(["node", "docassay", "init", "--help"])).toThrow();
    expect(logs.join("")).toContain("--force");
  });
});
