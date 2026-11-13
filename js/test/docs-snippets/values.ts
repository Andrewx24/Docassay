import { valuesEqual } from "../../src/normalize/index.js";

export const sameNumber = valuesEqual("$1,200.00", "1200");
export const sameDate = valuesEqual("Nov. 1 2026", "2026-11-01");
export const differentText = valuesEqual("Acme", "Acme Corp");
