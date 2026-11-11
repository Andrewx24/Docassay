import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { runsAsCli } from "../src/cli.js";

describe("runsAsCli", () => {
  it("follows a symlinked bin to the real file", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "docassay-cli-"));
    try {
      const real = path.join(dir, "cli.js");
      const link = path.join(dir, "docassay");
      writeFileSync(real, "");
      symlinkSync(real, link);
      expect(runsAsCli(link, real)).toBe(true);
      expect(runsAsCli(real, real)).toBe(true);
      expect(runsAsCli(path.join(dir, "missing.js"), real)).toBe(false);
      expect(runsAsCli(undefined, real)).toBe(false);
      expect(runsAsCli("", real)).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
