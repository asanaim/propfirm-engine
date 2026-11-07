import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ICONS } from "./icons";

describe("back-office icons (rule AC28)", () => {
  it("gives every place its own icon", () => {
    const glyphs = Object.values(ICONS);
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });

  it("gives every resource an icon and uses no Material icons", () => {
    const app = readFileSync(resolve(__dirname, "App.tsx"), "utf8");
    const resources = app.match(/<Resource name="[^"]+"[^>]*>/g) ?? [];
    expect(resources.length).toBeGreaterThan(0);
    for (const r of resources) expect(r).toContain("icon={icon(ICONS.");
    for (const file of ["App.tsx", "Menu.tsx", "Home.tsx"]) {
      expect(readFileSync(resolve(__dirname, file), "utf8")).not.toContain("@mui/icons-material");
    }
  });
});
