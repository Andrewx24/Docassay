import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { version } from "../src/version.js";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "../..");

describe("scaffold", () => {
  it("matches /VERSION", () => {
    const expected = readFileSync(join(repoRoot, "VERSION"), "utf8").trim();
    expect(version).toBe(expected);
  });
});
