import { describe, expect, it } from "vitest";

import { tokenCount } from "../src/tokenize/index.js";

describe("tokenCount", () => {
  it("counts non-whitespace runs after normalization", () => {
    expect(tokenCount("  one   two  ")).toBe(2);
    expect(tokenCount("")).toBe(0);
    expect(tokenCount("a\u00a0b")).toBe(2);
    expect(tokenCount("conti-\n nued")).toBe(1);
  });

  it("names the known tokenizers when the name is unknown", () => {
    expect(() => tokenCount("hello", "nope")).toThrow(/words/);
  });

  it("requires an encoding after tiktoken:", () => {
    expect(() => tokenCount("hello", "tiktoken:")).toThrow(/cl100k_base/);
  });
});
