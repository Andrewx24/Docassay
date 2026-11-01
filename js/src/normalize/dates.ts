/**
 * Date parsing to ISO YYYY-MM-DD. No locale and no timezone.
 *
 * @example
 * ```ts
 * parseDate("Nov. 1 2026") === "2026-11-01";
 * ```
 */

import { normText } from "./text.js";

const MONTHS: Record<string, number> = {
  january: 1,
  jan: 1,
  february: 2,
  feb: 2,
  march: 3,
  mar: 3,
  april: 4,
  apr: 4,
  may: 5,
  june: 6,
  jun: 6,
  july: 7,
  jul: 7,
  august: 8,
  aug: 8,
  september: 9,
  sep: 9,
  october: 10,
  oct: 10,
  november: 11,
  nov: 11,
  december: 12,
  dec: 12,
};

export type DateOrder = "mdy" | "dmy";

function isDigits(text: string): boolean {
  return text.length > 0 && [...text].every((ch) => ch >= "0" && ch <= "9");
}

function yearOf(part: string): number | null {
  if (part.length !== 4 || !isDigits(part)) {
    return null;
  }
  return Number(part);
}

function dayOrMonth(part: string): number | null {
  if (!isDigits(part) || part.length > 2) {
    return null;
  }
  return Number(part);
}

function isLeap(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function ymd(year: number, month: number, day: number): string | null {
  if (month < 1 || month > 12) {
    return null;
  }
  let last = 31;
  if (month === 2) {
    last = isLeap(year) ? 29 : 28;
  } else if (month === 4 || month === 6 || month === 9 || month === 11) {
    last = 30;
  }
  if (day < 1 || day > last) {
    return null;
  }
  const monthText = String(month).padStart(2, "0");
  const dayText = String(day).padStart(2, "0");
  return `${String(year).padStart(4, "0")}-${monthText}-${dayText}`;
}

function split3(text: string, separators: string): string[] | null {
  const used = [...separators].filter((sep) => text.includes(sep));
  if (used.length !== 1) {
    return null;
  }
  const sep = used[0] ?? "";
  const parts = text.split(sep);
  if (parts.length !== 3 || parts.some((part) => part === "")) {
    return null;
  }
  return parts;
}

function parseIso(text: string): string | null {
  const parts = split3(text, "-/");
  if (parts === null) {
    return null;
  }
  const year = yearOf(parts[0] ?? "");
  const month = dayOrMonth(parts[1] ?? "");
  const day = dayOrMonth(parts[2] ?? "");
  if (year === null || month === null || day === null) {
    return null;
  }
  return ymd(year, month, day);
}

function parseNumeric(text: string, order: DateOrder): string | null {
  const parts = split3(text, "-/.");
  if (parts === null) {
    return null;
  }
  const year = yearOf(parts[2] ?? "");
  const first = dayOrMonth(parts[0] ?? "");
  const second = dayOrMonth(parts[1] ?? "");
  if (year === null || first === null || second === null) {
    return null;
  }
  const month = order === "dmy" ? second : first;
  const day = order === "dmy" ? first : second;
  return ymd(year, month, day);
}

function monthToken(token: string): number | null {
  const name = token.endsWith(".") ? token.slice(0, -1) : token;
  if (token.endsWith(".") && name.length !== 3) {
    return null;
  }
  return MONTHS[name] ?? null;
}

function parseMonthName(text: string): string | null {
  if (!text) {
    return null;
  }
  const tokens = text.split(" ").map((token) => (token.endsWith(",") ? token.slice(0, -1) : token));
  if (tokens.length !== 3) {
    return null;
  }
  const year = yearOf(tokens[2] ?? "");
  if (year === null) {
    return null;
  }
  const leadingMonth = monthToken(tokens[0] ?? "");
  if (leadingMonth !== null) {
    const day = dayOrMonth(tokens[1] ?? "");
    if (day === null) {
      return null;
    }
    return ymd(year, leadingMonth, day);
  }
  const day = dayOrMonth(tokens[0] ?? "");
  const month = monthToken(tokens[1] ?? "");
  if (day === null || month === null) {
    return null;
  }
  return ymd(year, month, day);
}

/**
 * Parse a date to ISO YYYY-MM-DD, or return null.
 *
 * `order` is `mdy` or `dmy` and applies only to numeric dates whose year is
 * last. Two-digit years and invalid calendar dates return null.
 *
 * @example
 * ```ts
 * parseDate("Nov. 1 2026") === "2026-11-01";
 * ```
 */
export function parseDate(text: string, order: DateOrder = "mdy"): string | null {
  const normalized = normText(text);
  const iso = parseIso(normalized);
  if (iso !== null) {
    return iso;
  }
  const numeric = parseNumeric(normalized, order);
  if (numeric !== null) {
    return numeric;
  }
  return parseMonthName(normalized);
}
