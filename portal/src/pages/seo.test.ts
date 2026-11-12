import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/** The text written into the page before scripts run (src/propfirm/seo.py,
review D10) repeats the home page's built-in wording; this keeps the two in
step. Python joins long strings over lines, so spaces are compared loosely. */
describe("the server's copy of the home page wording", () => {
  const flat = (s: string) => s.replace(/"\s*\n\s*"/g, "").replace(/\s+/g, " ");
  const python = flat(readFileSync(resolve(__dirname, "../../../src/propfirm/seo.py"), "utf8"));
  const page = readFileSync(resolve(__dirname, "OverviewPage.tsx"), "utf8");

  it("has every built-in step, question, answer and hero line", () => {
    const block = (name: string) => page.slice(page.indexOf(`const ${name}`), page.indexOf("];", page.indexOf(`const ${name}`)));
    const strings = [block("STEPS"), block("FAQ")].flatMap((b) => [...b.matchAll(/(?:title|text|q|a): "([^"]+)"/g)].map((m) => m[1] ?? ""));
    const hero = [...page.matchAll(/\?\? "([^"]+)"/g)].map((m) => m[1] ?? "");
    const all = [...strings, ...hero.filter((h) => !["Challenges", "How it works", "In numbers", "Choose a challenge"].includes(h))];
    expect(all.length).toBeGreaterThan(15);
    for (const s of all) expect(python, s).toContain(s.replace(/\s+/g, " "));
  });
});
