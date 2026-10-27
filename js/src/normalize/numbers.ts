/**
 * Number parsing. See the Normalization doc for the step order.
 *
 * @example
 * ```ts
 * parseNumber("(350)") === -350;
 * ```
 */

import { isWhitespace, normText } from "./text.js";

const CURRENCY_SYMBOLS = new Set(["$", "€", "£", "¥", "₹", "₩", "₽", "¢"]);

const ISO_BLOCK = `
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN
BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK
DJF DKK DOP DZD
EGP ERN ETB EUR
FJD FKP
GBP GEL GHS GIP GMD GNF GTQ GYD
HKD HNL HTG HUF
IDR ILS INR IQD IRR ISK
JMD JOD JPY
KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD
MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN
NAD NGN NIO NOK NPR NZD
OMR
PAB PEN PGK PHP PKR PLN PYG
QAR
RON RSD RUB RWF
SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD STN SVC SYP SZL
THB TJS TMT TND TOP TRY TTD TWD TZS
UAH UGX USD UYU UZS
VES VND VUV
WST
XAF XCD XOF XPF
YER
ZAR ZMW ZWL
`;

function isoCodes(block: string): Set<string> {
  const found: string[] = [];
  for (const line of block.split("\n")) {
    for (const word of line.split(" ")) {
      if (word) {
        found.push(word);
      }
    }
  }
  return new Set(found);
}

const ISO_CODES = isoCodes(ISO_BLOCK);

export type DecimalMark = "." | ",";

function stripEnd(text: string, leading: boolean): string {
  if (!text) {
    return text;
  }
  if (leading) {
    if (CURRENCY_SYMBOLS.has(text[0] ?? "")) {
      return text.slice(1);
    }
    if (text.length >= 3 && ISO_CODES.has(text.slice(0, 3))) {
      return text.slice(3);
    }
    return text;
  }
  if (CURRENCY_SYMBOLS.has(text[text.length - 1] ?? "")) {
    return text.slice(0, -1);
  }
  if (text.length >= 3 && ISO_CODES.has(text.slice(-3))) {
    return text.slice(0, -3);
  }
  return text;
}

function applySign(text: string): string {
  if (text.length >= 2 && text.startsWith("(") && text.endsWith(")")) {
    text = `-${text.slice(1, -1)}`;
  }
  if (text.endsWith("-")) {
    const body = text.slice(0, -1);
    return body.startsWith("-") ? body : `-${body}`;
  }
  return text;
}

function applyDecimal(text: string, decimal: DecimalMark): string | null {
  const lastDot = text.lastIndexOf(".");
  const lastComma = text.lastIndexOf(",");
  if (lastDot >= 0 && lastComma >= 0) {
    const mark = lastDot > lastComma ? "." : ",";
    if (mark !== decimal) {
      return null;
    }
    const thousands = decimal === "." ? "," : ".";
    text = text.split(thousands).join("");
    if (decimal === ",") {
      text = text.replace(",", ".");
    }
    return text;
  }
  if (decimal === ".") {
    return text.split(",").join("");
  }
  return text.split(".").join("").replace(",", ".");
}

function isPlainNumber(text: string): boolean {
  if (!text) {
    return false;
  }
  let index = 0;
  if (text[0] === "-") {
    index = 1;
    if (index === text.length) {
      return false;
    }
  }
  let sawDot = false;
  let digits = 0;
  let fraction = 0;
  while (index < text.length) {
    const ch = text[index] ?? "";
    if (ch === ".") {
      if (sawDot || digits === 0) {
        return false;
      }
      sawDot = true;
    } else if (ch >= "0" && ch <= "9") {
      if (sawDot) {
        fraction += 1;
      } else {
        digits += 1;
      }
    } else {
      return false;
    }
    index += 1;
  }
  return !sawDot || fraction > 0;
}

/**
 * Parse a number, or return null when the text is not a number.
 *
 * @example
 * ```ts
 * parseNumber("1.200,00 €", ",") === 1200;
 * ```
 */
export function parseNumber(text: string, decimal: DecimalMark = "."): number | null {
  let cleaned = normText(text, false);
  cleaned = [...cleaned].filter((ch) => !isWhitespace(ch) && ch !== "'").join("");
  cleaned = stripEnd(cleaned, true);
  cleaned = stripEnd(cleaned, false);
  if (cleaned.endsWith("%")) {
    cleaned = cleaned.slice(0, -1);
  }
  cleaned = applySign(cleaned);
  const applied = applyDecimal(cleaned, decimal);
  if (applied === null || !isPlainNumber(applied)) {
    return null;
  }
  return Number(applied);
}
