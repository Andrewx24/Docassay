/**
 * Token counters used by the chunk scorer.
 *
 * @example
 * ```ts
 * import { tokenCount } from "./tokenize/index.js";
 *
 * tokenCount("one two three") === 3;
 * ```
 */

import { createRequire } from "node:module";

import { normText } from "../normalize/index.js";
import { isWhitespace } from "../normalize/text.js";

const require = createRequire(import.meta.url);

interface TiktokenEncoder {
  encode: (text: string) => readonly number[];
}

interface TiktokenModule {
  getEncoding: (encoding: string) => TiktokenEncoder;
}

/**
 * Count tokens in `text`.
 *
 * `words` counts maximal runs of non-whitespace characters after `normText`.
 * `tiktoken:<encoding>` uses that encoding and requires the optional
 * `tiktoken` peer dependency. That count is the encoding length of the
 * original text.
 *
 * @example
 * ```ts
 * tokenCount("one two three", "words") === 3;
 * ```
 */
export function tokenCount(text: string, tokenizer = "words"): number {
  if (tokenizer === "words") return words(text);
  const prefix = "tiktoken:";
  if (tokenizer.startsWith(prefix)) {
    const encoding = tokenizer.slice(prefix.length);
    if (encoding === "") {
      throw new Error(
        'tokenizer "tiktoken:" needs an encoding, for example "tiktoken:cl100k_base".',
      );
    }
    return tiktokenCount(text, encoding);
  }
  throw new Error(`unknown tokenizer ${JSON.stringify(tokenizer)}. Use "words" or "tiktoken:<encoding>".`);
}

function words(text: string): number {
  let count = 0;
  let inRun = false;
  for (const char of normText(text)) {
    if (isWhitespace(char)) {
      inRun = false;
      continue;
    }
    if (!inRun) {
      count += 1;
      inRun = true;
    }
  }
  return count;
}

function tiktokenCount(text: string, encoding: string): number {
  let loaded: TiktokenModule;
  try {
    loaded = require("tiktoken") as TiktokenModule;
  } catch {
    throw new Error(
      `tokenizer "tiktoken:${encoding}" requires the optional tiktoken peer dependency. Install tiktoken.`,
    );
  }
  let encoder: TiktokenEncoder;
  try {
    encoder = loaded.getEncoding(encoding);
  } catch {
    throw new Error(
      `unknown tiktoken encoding ${JSON.stringify(encoding)}. Use an encoding name such as cl100k_base.`,
    );
  }
  return encoder.encode(text).length;
}
