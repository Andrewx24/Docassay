import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fence = /```([^\n]*)\n(.*?)```/gs;
const title = /title="([^"]+)"/;

function pages() {
  const guides = path.join(repo, "docs", "docs", "guides");
  const found = [path.join(repo, "README.md")];
  for (const name of readdirSync(guides)) {
    const full = path.join(guides, name);
    if (statSync(full).isFile() && (name.endsWith(".md") || name.endsWith(".mdx"))) found.push(full);
  }
  return found;
}

const referenced = new Set();
for (const page of pages()) {
  const text = readFileSync(page, "utf8");
  for (const match of text.matchAll(fence)) {
    const info = match[1] ?? "";
    const titled = title.exec(info);
    if (titled === null) {
      console.error(`${path.relative(repo, page)}: code block has no title: ${info}`);
      process.exit(1);
    }
    const source = path.join(repo, titled[1]);
    const body = match[2] ?? "";
    const expected = readFileSync(source, "utf8");
    if (body !== expected) {
      console.error(`${path.relative(repo, page)} drifted from ${titled[1]}`);
      process.exit(1);
    }
    referenced.add(titled[1]);
  }
}

for (const required of [
  "python/tests/docs_snippets/quickstart.sh",
  "examples/workflows/pr-check.yml",
]) {
  if (!referenced.has(required)) {
    console.error(`no guide copies ${required}`);
    process.exit(1);
  }
}

console.log(`docs snippets match (${referenced.size} blocks)`);
