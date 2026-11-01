/**
 * Normalization used by every comparison.
 *
 * @example
 * ```ts
 * import { valuesEqual } from "./normalize/index.js";
 * valuesEqual("Nov. 1 2026", "2026-11-01");
 * ```
 */

import { parseDate, type DateOrder } from "./dates.js";
import { parseNumber, type DecimalMark } from "./numbers.js";
import { normText } from "./text.js";

export { normText } from "./text.js";
export { parseNumber, type DecimalMark } from "./numbers.js";
export { parseDate, type DateOrder } from "./dates.js";

export interface CompareOptions {
  /** Decimal mark, `"."` or `","`. */
  decimal?: DecimalMark;
  /** Numeric date order. `mdy` or `dmy`. */
  dateOrder?: DateOrder;
}

/**
 * Round to 4 decimal places with floor(x * 10000 + 0.5) / 10000.
 *
 * @example
 * ```ts
 * round4(1.23456) === 1.2346;
 * ```
 */
export function round4(value: number): number {
  return Math.floor(value * 10000 + 0.5) / 10000;
}

/**
 * Compare two values as numbers, then dates, then normalized text.
 *
 * A number is never equal to a date. Numeric equality is
 * abs(a - b) <= 1e-6 * max(1, abs(b)), with b the actual value.
 *
 * @example
 * ```ts
 * valuesEqual("(350)", "-350") === true;
 * ```
 */
export function valuesEqual(expected: string, actual: string, opts?: CompareOptions): boolean {
  const decimal = opts?.decimal ?? ".";
  const order = opts?.dateOrder ?? "mdy";
  const expectedNumber = parseNumber(expected, decimal);
  const actualNumber = parseNumber(actual, decimal);
  if (expectedNumber !== null && actualNumber !== null) {
    return Math.abs(expectedNumber - actualNumber) <= 1e-6 * Math.max(1, Math.abs(actualNumber));
  }
  const expectedDate = parseDate(expected, order);
  const actualDate = parseDate(actual, order);
  if (expectedDate !== null && actualDate !== null) {
    return expectedDate === actualDate;
  }
  const numberVsDate =
    (expectedNumber !== null && actualDate !== null) ||
    (expectedDate !== null && actualNumber !== null);
  if (numberVsDate) {
    return false;
  }
  return normText(expected) === normText(actual);
}
