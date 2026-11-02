/**
 * Markdown scorecard for a pull request comment.
 *
 * The first line is the hidden marker `<!-- docassay-report -->`. The body is
 * a compact table plus a collapsible failure list.
 *
 * @example
 * ```ts
 * import { renderMarkdown } from "./report/markdown.js";
 *
 * renderMarkdown(card, [3]);
 * ```
 */

import { version } from "../version.js";
import type { Failure, Scorecard } from "../models/scorecard.js";
import { METRICS } from "../scoring/aggregate.js";
import {
  costPerThousand,
  formatCost,
  formatLatency,
  formatScore4,
  metricValue,
  sortedParsers,
} from "./terminal.js";

export const MARKER = "<!-- docassay-report -->";
const HEADERS = ["parser", ...METRICS, "overall", "$/1k pages", "p95"];

/**
 * Return a GitHub-flavored Markdown report.
 *
 * @example
 * ```ts
 * const text = renderMarkdown(card, [1]);
 * ```
 */
export function renderMarkdown(card: Scorecard, pages: readonly number[]): string {
  const count = card.parsers[0]?.documents.length ?? 0;
  const lines = [
    MARKER,
    "",
    `# docassay ${version}`,
    "",
    `${countPhrase(count, "document")} · ${countPhrase(card.parsers.length, "parser")}`,
    "",
    `| ${HEADERS.join(" | ")} |`,
    `| ${HEADERS.map(() => "---").join(" | ")} |`,
  ];
  const failures: string[] = [];
  for (const [parser, pageCount] of sortedParsers(card, pages)) {
    const cells = [parser.parser];
    for (const name of [...METRICS, "overall"]) cells.push(formatScore4(metricValue(parser.summary, name)));
    cells.push(formatCost(costPerThousand(parser.costUsd, pageCount)));
    cells.push(formatLatency(parser.latencyMsP95));
    lines.push(`| ${cells.map(cell).join(" | ")} |`);
    for (const document of parser.documents) {
      if (document.error) {
        failures.push(`- **${cell(parser.parser)}** \`${cell(document.id)}\` error: ${cell(document.error)}`);
      }
      for (const failure of document.failures) failures.push(failureLine(parser.parser, document.id, failure));
    }
  }
  lines.push("", "<details>", `<summary>Failures (${failures.length})</summary>`, "");
  lines.push(...(failures.length > 0 ? failures : ["None."]));
  lines.push("", "</details>", "");
  return `${lines.join("\n")}\n`;
}

function failureLine(parser: string, docId: string, failure: Failure): string {
  return (
    `- **${cell(parser)}** \`${cell(docId)}\` ${failure.metric} ` +
    `\`${cell(failure.check)}\`: ${cell(failure.reason)}`
  );
}

function countPhrase(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function cell(value: string): string {
  return value.replaceAll("|", "\\|").replaceAll("\n", " ");
}
