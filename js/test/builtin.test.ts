import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { BuiltinError, builtinConfigPath, configForGoldenFlag } from "../src/builtin.js";
import { createProgram } from "../src/cli.js";

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

describe("builtin golden set", () => {
  it("rejects an unknown --golden value", async () => {
    const result = await capture(["run", "--golden", "nope"]);
    expect(result.code).toBe(2);
    expect(result.stdout).toContain('unknown --golden value "nope"');
  });

  it("finds a checkout and prefers the packaged copy", () => {
    const root = path.join(tmpdir(), `docassay-builtin-${process.pid}`);
    rmSync(root, { recursive: true, force: true });
    try {
      const origin = path.join(root, "pkg");
      mkdirSync(origin, { recursive: true });
      expect(() => builtinConfigPath(origin)).toThrow(BuiltinError);
      const checkout = path.join(root, "golden");
      mkdirSync(checkout);
      writeFileSync(path.join(checkout, "docassay.yaml"), "version: 1\n");
      expect(builtinConfigPath(origin)).toBe(path.join(checkout, "docassay.yaml"));
      const packaged = path.join(root, "builtin-golden", "docassay.yaml");
      mkdirSync(path.dirname(packaged));
      writeFileSync(packaged, "version: 1\n");
      expect(path.resolve(builtinConfigPath(origin))).toBe(path.resolve(packaged));
      expect(configForGoldenFlag("docassay.yaml", undefined)).toBe("docassay.yaml");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
