import { describe, expect, it } from "vitest";

import { aggregateScores, documentScores, mean, percentile } from "../src/scoring/aggregate.js";

describe("aggregate", () => {
  it("averages metrics and percentiles", () => {
    expect(mean([1, 0])).toBe(0.5);
    expect(percentile([10, 20, 30], 0.5)).toBe(20);
    expect(percentile([10], 0.95)).toBe(10);
    expect(percentile([10, 20, 30], 0.95)).toBe(29);
    expect(() => mean([])).toThrow(/at least one/);
    expect(() => percentile([], 0.5)).toThrow(/at least one/);
    expect(() => percentile([1], 1.5)).toThrow(/between 0 and 1/);
  });

  it("sets document overall and summary overall separately", () => {
    expect(documentScores({})).toEqual({});
    expect(documentScores({ tables: 1, text: 0 }).overall).toBe(0.5);
    expect(
      aggregateScores([
        { tables: 1, overall: 1 },
        { text: 0, overall: 0 },
      ]),
    ).toEqual({ tables: 1, text: 0, overall: 0.5 });
    expect(aggregateScores([])).toEqual({});
  });
});
