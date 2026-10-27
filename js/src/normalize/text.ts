/**
 * Text normalization. Steps run in the order defined in the Normalization doc.
 *
 * @example
 * ```ts
 * normText("conti-\n nued") === "continued";
 * ```
 */

const DELETED = new Set(["\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"]);
const SINGLE_QUOTES = new Set(["\u2018", "\u2019", "\u201a", "\u201b", "\u2032"]);
const DOUBLE_QUOTES = new Set(["\u201c", "\u201d", "\u201e", "\u201f", "\u2033"]);
const SPACE_OR_TAB = new Set([" ", "\t"]);

/**
 * Return whether `ch` is in the explicit whitespace set.
 *
 * @example
 * ```ts
 * isWhitespace("\u00a0") === true;
 * ```
 */
export function isWhitespace(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  return (
    (code >= 0x0009 && code <= 0x000d) ||
    code === 0x0020 ||
    code === 0x0085 ||
    code === 0x00a0 ||
    code === 0x1680 ||
    (code >= 0x2000 && code <= 0x200a) ||
    code === 0x2028 ||
    code === 0x2029 ||
    code === 0x202f ||
    code === 0x205f ||
    code === 0x3000
  );
}

function isLetter(ch: string): boolean {
  return /^\p{L}$/u.test(ch);
}

function isDash(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  return (code >= 0x2010 && code <= 0x2015) || code === 0x2212;
}

function dehyphenate(text: string): string {
  const chars = [...text];
  const out: string[] = [];
  let index = 0;
  while (index < chars.length) {
    const previous = out[out.length - 1];
    if (chars[index] === "-" && previous !== undefined && isLetter(previous)) {
      let cursor = index + 1;
      while (cursor < chars.length && SPACE_OR_TAB.has(chars[cursor] ?? "")) {
        cursor += 1;
      }
      if (chars[cursor] === "\n") {
        cursor += 1;
        while (cursor < chars.length && isWhitespace(chars[cursor] ?? "")) {
          cursor += 1;
        }
        const next = chars[cursor];
        if (next !== undefined && isLetter(next)) {
          index = cursor;
          continue;
        }
      }
    }
    const current = chars[index];
    if (current !== undefined) {
      out.push(current);
    }
    index += 1;
  }
  return out.join("");
}

function collapseWhitespace(text: string): string {
  const out: string[] = [];
  let inRun = false;
  for (const ch of text) {
    if (isWhitespace(ch)) {
      inRun = true;
      continue;
    }
    if (inRun) {
      out.push(" ");
      inRun = false;
    }
    out.push(ch);
  }
  if (inRun) {
    out.push(" ");
  }
  const collapsed = out.join("");
  let start = 0;
  let end = collapsed.length;
  while (start < end && collapsed[start] === " ") {
    start += 1;
  }
  while (end > start && collapsed[end - 1] === " ") {
    end -= 1;
  }
  return collapsed.slice(start, end);
}

/**
 * Normalize text before comparison.
 *
 * `lower` false skips the final lowercasing step. `parseNumber` uses that so
 * currency codes stay uppercase.
 *
 * @example
 * ```ts
 * normText("conti-\n nued") === "continued";
 * ```
 */
export function normText(text: string, lower = true): string {
  let normalized = text.normalize("NFKC");
  normalized = [...normalized].filter((ch) => !DELETED.has(ch)).join("");
  let mapped = "";
  for (const ch of normalized) {
    if (SINGLE_QUOTES.has(ch)) {
      mapped += "'";
    } else if (DOUBLE_QUOTES.has(ch)) {
      mapped += '"';
    } else if (isDash(ch)) {
      mapped += "-";
    } else {
      mapped += ch;
    }
  }
  const collapsed = collapseWhitespace(dehyphenate(mapped));
  return lower ? collapsed.toLowerCase() : collapsed;
}
