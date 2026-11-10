import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

// A stylesheet with an unclosed block still loads, but every rule after the
// break is lost (this happened once in a merge); keep the blocks balanced.
it("closes every block it opens", () => {
  const css = readFileSync(resolve(__dirname, "portal.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  let depth = 0;
  for (const ch of css) {
    if (ch === "{") depth++;
    if (ch === "}") depth--;
    expect(depth).toBeGreaterThanOrEqual(0);
  }
  expect(depth).toBe(0);
});
