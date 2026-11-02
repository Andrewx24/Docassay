import { describe, expect, it } from "vitest";

import type { Scorecard } from "../src/models/scorecard.js";
import { renderHtml } from "../src/report/html.js";
import { formatCreated, renderJson } from "../src/report/json.js";
import { MARKER, renderMarkdown } from "../src/report/markdown.js";
import {
  EM_DASH,
  costPerThousand,
  formatCost,
  formatLatency,
  renderTerminal,
  sortedParsers,
} from "../src/report/terminal.js";

const card: Scorecard = {
  specVersion: "1.0.0",
  docassayVersion: "0.0.0",
  runId: "run_test",
  createdAt: "2026-11-01T18:00:00.000Z",
  parsers: [
    {
      parser: "docling",
      adapter: "docling",
      parserVersion: "1",
      summary: { tables: 0.81, text: 0.97, order: 0.92, overall: 0.9 },
      costUsd: 0,
      latencyMsP50: 1000,
      latencyMsP95: 6100,
      documents: [
        {
          id: "scan-lease-2019",
          file: "files/scan.pdf",
          scores: { tables: 0.42, overall: 0.42 },
          failures: [{ metric: "tables", check: "tables[0]", reason: "no table found" }],
        },
      ],
    },
    {
      parser: "llamaparse:agentic",
      adapter: "llamaparse:agentic",
      parserVersion: "2026-09-24",
      summary: { tables: 0.94, text: 0.99, order: 0.95, fields: 0.96, overall: 0.96 },
      costUsd: 0.025,
      latencyMsP50: 800,
      latencyMsP95: 48000,
      documents: [
        {
          id: "scan-lease-2019",
          file: "files/scan.pdf",
          scores: { tables: 0.58, overall: 0.58 },
          failures: [{ metric: "tables", check: "tables[0]", reason: "header does not match" }],
        },
      ],
    },
  ],
};

describe("reports", () => {
  it("lays out the terminal table", () => {
    const text = renderTerminal(card, [2, 2]);
    expect(text.startsWith("docassay 0.0.0 · 1 document · 2 parsers\n")).toBe(true);
    expect(text.indexOf("llamaparse:agentic")).toBeLessThan(text.indexOf("docling"));
    expect(text).toContain(EM_DASH);
    expect(text).toContain("12.50");
    expect(text).toContain("48.0s");
    expect(text).toContain("6.1s");
    expect(text).toContain("tables 0.42 (docling), 0.58 (llamaparse:agentic)");
    expect(text).toContain("Run with -v for every failure.");
    expect(text).not.toContain("Failures");
  });

  it("matches the CLI page table", () => {
    const page = structuredClone(card);
    const docling = page.parsers[0];
    const llama = page.parsers[1];
    if (docling === undefined || llama === undefined) throw new Error("missing parser");
    llama.costUsd = 0.25;
    docling.documents.push({
      id: "10k-segment-table",
      file: "files/table.pdf",
      scores: { order: 0.5, overall: 0.5 },
      failures: [{ metric: "order", check: "order[0]", reason: "out of order" }],
    });
    llama.documents.push({
      id: "10k-segment-table",
      file: "files/table.pdf",
      scores: { order: 1, overall: 1 },
      failures: [],
    });
    page.parsers.push({
      parser: "reducto",
      adapter: "reducto",
      parserVersion: "1",
      summary: { tables: 0.95, text: 0.99, order: 0.96, fields: 0.98, chunks: 0.91, overall: 0.96 },
      costUsd: 0.2,
      latencyMsP95: 9100,
      documents: [
        {
          id: "scan-lease-2019",
          file: "files/scan.pdf",
          scores: { tables: 1, overall: 1 },
          failures: [],
        },
        {
          id: "10k-segment-table",
          file: "files/table.pdf",
          scores: { order: 1, overall: 1 },
          failures: [],
        },
      ],
    });
    const lines = renderTerminal(page, [20, 20, 20]).split("\n").slice(2, 10).join("\n");
    expect(lines).toBe(
      [
        "parser              tables  text  order  fields  chunks  overall  $/1k pages  p95",
        "llamaparse:agentic  0.94    0.99  0.95   0.96    —       0.96     12.50       48.0s",
        "reducto             0.95    0.99  0.96   0.98    0.91    0.96     10.00        9.1s",
        "docling             0.81    0.97  0.92   —       —       0.90      0.00        6.1s",
        "",
        "Worst documents",
        "  scan-lease-2019    tables 0.42 (docling), 0.58 (llamaparse:agentic)",
        "  10k-segment-table  order 0.50 (docling)",
      ].join("\n"),
    );
  });

  it("prints failures only when verbose and stays quiet when every score is 1", () => {
    const verbose = renderTerminal(card, [2, 2], true);
    expect(verbose).toContain("no table found");
    expect(verbose).toContain("header does not match");
    expect(verbose).not.toContain("Run with -v");
    const perfect: Scorecard = {
      ...card,
      parsers: [
        {
          parser: "file",
          adapter: "file",
          parserVersion: "file",
          summary: { text: 1, overall: 1 },
          documents: [{ id: "note", file: "note.pdf", scores: { text: 1, overall: 1 }, failures: [] }],
        },
      ],
    };
    const quiet = renderTerminal(perfect, [1]);
    expect(quiet).not.toContain("Worst documents");
    expect(quiet).not.toContain("Run with -v");
  });

  it("writes markdown and a self-contained html file", () => {
    const markdown = renderMarkdown(card, [2, 2]);
    expect(markdown.startsWith(`${MARKER}\n`)).toBe(true);
    expect(markdown).toContain("<details>");
    expect(markdown).toContain("<summary>Failures (2)</summary>");
    expect(markdown).toContain("1 document · 2 parsers");
    const docling = card.parsers[0];
    const llama = card.parsers[1];
    if (docling === undefined || llama === undefined) throw new Error("missing parser");
    const named: Scorecard = {
      ...card,
      parsers: [{ ...docling, parser: "<b>" }, llama],
    };
    const html = renderHtml(named, [2, 2], { "scan-lease-2019": ["lease"] });
    expect(html).not.toContain("http");
    expect(html).not.toContain("<link");
    expect(html).toContain('id="parser"');
    expect(html).toContain('id="tag"');
    expect(html).toContain('id="metric"');
    expect(html).toContain("&lt;b&gt;");
    expect(html).toContain("lease");
  });

  it("serializes stable json numbers", () => {
    const payload = JSON.parse(renderJson(card)) as { createdAt: string; parsers: Array<Record<string, unknown>> };
    expect(payload.createdAt).toBe("2026-11-01T18:00:00Z");
    expect(payload.parsers[0]?.costUsd).toBe(0);
    expect(payload.parsers[1]?.latencyMsP95).toBe(48000);
    expect(formatCreated("2026-11-01T18:00:00.900Z")).toBe("2026-11-01T18:00:00Z");
    const docling = card.parsers[0];
    const llama = card.parsers[1];
    if (docling === undefined || llama === undefined) throw new Error("missing parser");
    const document = docling.documents[0];
    const failure = document?.failures[0];
    if (document === undefined || failure === undefined) throw new Error("missing failure");
    const nested: Scorecard = {
      ...card,
      parsers: [
        {
          ...docling,
          documents: [
            {
              ...document,
              failures: [
                {
                  ...failure,
                  expected: { rows: [1, { n: 2.5 }], flag: true },
                },
              ],
            },
          ],
        },
        llama,
      ],
    };
    const encoded = JSON.parse(renderJson(nested)) as {
      parsers: Array<{ documents: Array<{ failures: Array<{ expected: unknown }> }> }>;
    };
    expect(encoded.parsers[0]?.documents[0]?.failures[0]?.expected).toEqual({
      rows: [1, { n: 2.5 }],
      flag: true,
    });
    expect(formatCost(undefined)).toBe(EM_DASH);
    expect(formatLatency(undefined)).toBe(EM_DASH);
    expect(costPerThousand(1, 0)).toBeUndefined();
    expect(() => sortedParsers(card, [1])).toThrow(/page counts/);
  });
});
