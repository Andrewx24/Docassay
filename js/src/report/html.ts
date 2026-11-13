/**
 * Self-contained HTML scorecard.
 *
 * The file embeds its CSS and script. Filters hide rows by parser, tag, and
 * metric. Nothing is loaded from the network.
 *
 * @example
 * ```ts
 * import { renderHtml } from "./report/html.js";
 *
 * renderHtml(card, [3], { "invoice-acme": ["invoice"] });
 * ```
 */

import { version } from "../version.js";
import type { DocumentResult, ParserResult, Scorecard } from "../models/scorecard.js";
import { METRICS } from "../scoring/aggregate.js";
import {
  costPerThousand,
  formatCost,
  formatLatency,
  formatScore4,
  metricValue,
  sortedParsers,
} from "./terminal.js";

const HEADERS = ["parser", "document", ...METRICS, "overall", "error"];
const LEFT = new Set([0, 1, HEADERS.length - 1]);

/**
 * Return one HTML document with parser, tag, and metric filters.
 *
 * @example
 * ```ts
 * renderHtml(card, [1], {});
 * ```
 */
export function renderHtml(
  card: Scorecard,
  pages: readonly number[],
  tags: Readonly<Record<string, readonly string[]>>,
): string {
  const title = `docassay ${version}`;
  const count = card.parsers[0]?.documents.length ?? 0;
  const ordered = sortedParsers(card, pages);
  const parserNames = ordered.map(([parser]) => parser.parser);
  const tagNames = [...new Set(Object.values(tags).flat())].sort();
  const rows = ordered.map(([parser, pageCount]) => summaryRow(parser, pageCount));
  for (const [parser] of ordered) {
    for (const document of parser.documents) {
      rows.push(documentRow(parser.parser, document, tags[document.id] ?? []));
    }
  }
  return (
    "<!DOCTYPE html>\n" +
    '<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
    `<title>${text(title)}</title>\n<style>\n${STYLE}</style>\n</head>\n<body>\n` +
    `<h1>${text(title)}</h1>\n` +
    `<p>${countPhrase(count, "document")} · ${countPhrase(card.parsers.length, "parser")}</p>\n` +
    '<div class="filters">\n' +
    `<label>Parser ${select("parser", parserNames)}</label>\n` +
    `<label>Tag ${select("tag", tagNames)}</label>\n` +
    `<label>Metric ${select("metric", [...METRICS])}</label>\n` +
    "</div>\n<table>\n<thead><tr>" +
    HEADERS.map((header, index) => headerCell(index, header)).join("") +
    "</tr></thead>\n<tbody>\n" +
    rows.join("\n") +
    "\n</tbody>\n</table>\n<script>\n" +
    SCRIPT +
    "</script>\n</body>\n</html>\n"
  );
}

function summaryRow(parser: ParserResult, pages: number): string {
  const cost = formatCost(costPerThousand(parser.costUsd, pages));
  const latency = formatLatency(parser.latencyMsP95);
  const label = `summary · ${cost} /1k pages · p95 ${latency}`;
  const cells = [...METRICS, "overall"]
    .map((name) => `<td>${text(formatScore4(metricValue(parser.summary, name)))}</td>`)
    .join("");
  return (
    `<tr data-parser="${text(parser.parser)}" data-tags="" data-metrics="" data-kind="summary">` +
    `<td class="left">${text(parser.parser)}</td><td class="left">${text(label)}</td>${cells}<td></td></tr>`
  );
}

function documentRow(parser: string, document: DocumentResult, tags: readonly string[]): string {
  const metrics = METRICS.filter((name) => metricValue(document.scores, name) !== undefined);
  const cells = [...METRICS, "overall"]
    .map((name) => `<td>${text(formatScore4(metricValue(document.scores, name)))}</td>`)
    .join("");
  return (
    `<tr data-parser="${text(parser)}" data-tags="${text(tags.join("\u001f"))}"` +
    ` data-metrics="${text(metrics.join(" "))}" data-kind="document">` +
    `<td class="left">${text(parser)}</td><td class="left">${text(document.id)}</td>${cells}` +
    `<td class="left">${text(document.error ?? "")}</td></tr>`
  );
}

function headerCell(index: number, header: string): string {
  const kind = LEFT.has(index) ? ' class="left"' : "";
  return `<th${kind}>${text(header)}</th>`;
}

function select(id: string, options: readonly string[]): string {
  const parts = ['<option value="all">all</option>'];
  for (const option of options) parts.push(`<option value="${text(option)}">${text(option)}</option>`);
  return `<select id="${id}">${parts.join("")}</select>`;
}

function countPhrase(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function text(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const STYLE =
  "body{font:14px/1.4 sans-serif;margin:24px;color:#1b1b1b}\n" +
  "table{border-collapse:collapse;width:100%}\n" +
  "th,td{border:1px solid #ccc;padding:4px 8px;text-align:right}\n" +
  "th.left,td.left{text-align:left}\n" +
  ".filters{display:flex;gap:16px;margin:12px 0}\n" +
  "tr[hidden]{display:none}\n";

const SCRIPT = `const parser = document.getElementById("parser");
const tag = document.getElementById("tag");
const metric = document.getElementById("metric");
function apply() {
  const parserValue = parser.value;
  const tagValue = tag.value;
  const metricValue = metric.value;
  document.querySelectorAll("tbody tr").forEach(function (row) {
    const parserOk = parserValue === "all" || row.dataset.parser === parserValue;
    const tags = (row.dataset.tags || "").split("\\u001f").filter(Boolean);
    const tagOk = tagValue === "all" || tags.indexOf(tagValue) !== -1;
    const metrics = (row.dataset.metrics || "").split(" ").filter(Boolean);
    const metricOk = metricValue === "all" || metrics.indexOf(metricValue) !== -1;
    const summary = row.dataset.kind === "summary";
    row.hidden = summary ? !parserOk : !(parserOk && tagOk && metricOk);
  });
}
parser.addEventListener("change", apply);
tag.addEventListener("change", apply);
metric.addEventListener("change", apply);
`;
