import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  normText,
  parseDate,
  parseNumber,
  round4,
  valuesEqual,
  type DateOrder,
  type DecimalMark,
} from "../src/normalize/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, "../../spec/fixtures/normalize");
const source = join(here, "../src/normalize");

interface Named {
  name: string;
}

function load<T>(name: string): T[] {
  return JSON.parse(readFileSync(join(fixtures, name), "utf8")) as T[];
}

function close(actual: number, expected: number): void {
  expect(Math.abs(actual - expected)).toBeLessThanOrEqual(1e-9);
}

describe("normalize fixtures", () => {
  it("has at least 60 cases", () => {
    const names = ["text.json", "numbers.json", "dates.json", "values_equal.json", "round4.json"];
    const total = names.reduce((sum, name) => sum + load<Named>(name).length, 0);
    expect(total).toBeGreaterThanOrEqual(60);
  });

  it.each(load<{ name: string; input: string; expected: string }>("text.json"))(
    "normText $name",
    ({ input, expected }) => {
      expect(normText(input)).toBe(expected);
    },
  );

  it.each(
    load<{ name: string; input: string; decimal: DecimalMark; expected: number | null }>(
      "numbers.json",
    ),
  )("parseNumber $name", ({ input, decimal, expected }) => {
    const parsed = parseNumber(input, decimal);
    if (expected === null) {
      expect(parsed).toBeNull();
    } else {
      expect(parsed).not.toBeNull();
      close(parsed ?? 0, expected);
    }
  });

  it.each(
    load<{ name: string; input: string; order: DateOrder; expected: string | null }>("dates.json"),
  )("parseDate $name", ({ input, order, expected }) => {
    expect(parseDate(input, order)).toBe(expected);
  });

  it.each(
    load<{
      name: string;
      expected: string;
      actual: string;
      decimal: DecimalMark;
      date_order: DateOrder;
      equal: boolean;
    }>("values_equal.json"),
  )("valuesEqual $name", (row) => {
    expect(
      valuesEqual(row.expected, row.actual, { decimal: row.decimal, dateOrder: row.date_order }),
    ).toBe(row.equal);
  });

  it.each(load<{ name: string; input: string; expected: number }>("round4.json"))(
    "round4 $name",
    ({ input, expected }) => {
      close(round4(Number(input)), expected);
    },
  );

  it("does not use ambiguous character classes", () => {
    const needles = ["s", "w", "b", "d"].map((name) => `${String.fromCharCode(92)}${name}`);
    needles.push("isspace");
    for (const file of readdirSync(source)) {
      if (!file.endsWith(".ts")) {
        continue;
      }
      const text = readFileSync(join(source, file), "utf8");
      for (const needle of needles) {
        expect(text.includes(needle), `${file} contains ${needle}`).toBe(false);
      }
    }
  });
});
