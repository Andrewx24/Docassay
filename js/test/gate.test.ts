import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { checkCommand, compare, makeBaseline } from "../src/gate.js";
import type { Baseline } from "../src/models/baseline.js";
import type { Scorecard } from "../src/models/scorecard.js";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixtures = path.join(repoRoot, "spec", "fixtures", "gate");
const pinned = new Date("2026-11-03T12:00:00Z");

function load(caseDir: string, name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path.join(caseDir, name), "utf8")) as Record<string, unknown>;
}

describe("gate", () => {
  it("matches every fixture", () => {
    for (const name of readdirSync(fixtures).sort()) {
      const caseDir = path.join(fixtures, name);
      const scorecard = load(caseDir, "scorecard.json") as unknown as Scorecard;
      const baseline = load(caseDir, "baseline.json") as unknown as Baseline;
      const expected = load(caseDir, "expected.json");
      const result = compare(scorecard, baseline, loadConfig(path.join(caseDir, "config.yaml")).thresholds);
      expect(result.exitCode, name).toBe(expected.exitCode);
      expect(result.text, name).toBe(expected.text);
    }
    const over = path.join(fixtures, "doc-over");
    const skipped = compare(
      load(over, "scorecard.json") as unknown as Scorecard,
      load(over, "baseline.json") as unknown as Baseline,
      loadConfig(path.join(over, "config.yaml")).thresholds,
      true,
    );
    expect(skipped.exitCode).toBe(0);
    expect(skipped.text).toBe(
      "0 regressions · 0 warnings · run `docassay baseline` after an intended change\n",
    );
  });

  it("writes a sorted baseline without failures", () => {
    const text = makeBaseline(load(path.join(fixtures, "doc-over"), "scorecard.json") as unknown as Scorecard, pinned);
    expect(text).not.toContain("failures");
    expect(text.indexOf('"adapter"')).toBeLessThan(text.indexOf('"documents"'));
    expect(text).toContain('"pinnedAt": "2026-11-03T12:00:00Z"');
    const frozen = readFileSync(path.join(fixtures, "no-change", "baseline-out.json"), "utf8");
    const source = load(path.join(fixtures, "no-change"), "scorecard.json") as unknown as Scorecard;
    expect(makeBaseline(source, pinned)).toBe(frozen);
  });

  it("keeps the failure detail on a floor that is not a drop", () => {
    const scorecard = load(path.join(fixtures, "no-change"), "scorecard.json");
    const baseline = load(path.join(fixtures, "no-change"), "baseline.json");
    const parser = (scorecard.parsers as Array<Record<string, unknown>>)[0];
    const document = (parser?.documents as Array<Record<string, unknown>>)[0];
    const earlier = (baseline.parsers as Array<Record<string, unknown>>)[0];
    const earlierDoc = (earlier?.documents as Array<Record<string, unknown>>)[0];
    if (document === undefined || earlierDoc === undefined) throw new Error("fixture document missing");
    document.scores = { tables: 0.5 };
    document.failures = [
      { metric: "tables", check: "tables[0]", expected: "1", actual: "0", reason: "too low" },
    ];
    earlierDoc.scores = { tables: 0.5 };
    const result = compare(
      scorecard as unknown as Scorecard,
      baseline as unknown as Baseline,
      { default: { min: 0.8 } },
    );
    expect(result.exitCode).toBe(1);
    expect(result.text).toBe(
      "FAIL  docling · invoice-acme · tables 0.5000 → 0.5000 (below floor 0.8000)\n" +
        '      tables[0]: expected "1", got "0"\n' +
        "\n" +
        "1 regression · 0 warnings · run `docassay baseline` after an intended change\n",
    );
    document.error = "parser timed out";
    expect(makeBaseline(scorecard as unknown as Scorecard, pinned)).toContain('"error": "parser timed out"');
  });

  it("reports a missing baseline", async () => {
    const root = path.join(repoRoot, "spec", "fixtures", "gate", "no-change");
    expect(await checkCommand(path.join(root, "config.yaml"), { baseline: path.join(root, "missing.json") })).toBe(2);
  });
});
