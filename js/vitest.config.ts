import { readFileSync } from "node:fs";

import { defineConfig } from "vitest/config";

const version = (
  JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as {
    version: string;
  }
).version;

export default defineConfig({
  define: {
    __DOCASSAY_VERSION__: JSON.stringify(version),
  },
  test: {
    include: ["test/**/*.test.ts"],
  },
});
