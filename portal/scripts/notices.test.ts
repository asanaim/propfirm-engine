import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { collect, render } from "./third-party-notices.mjs";

it("collects the texts of every shipped package, with the vendored NOTICE", () => {
  const shipped = collect(resolve(__dirname, ".."));
  const names = shipped.map((p) => p.name);
  for (const name of ["react", "react-dom", "oidc-client-ts", "lightweight-charts", "fancy-canvas", "lucide-react"]) {
    expect(names).toContain(name);
  }
  expect(names).toContain("@fontsource-variable/inter");
  expect(names.some((n) => n.startsWith("@types/") || n === "csstype" || n === "vite")).toBe(false);
  const text = render(shipped);
  expect(text).toContain("TradingView Lightweight Charts™\nCopyright (с) 2025 TradingView, Inc. https://www.tradingview.com/");
  expect(text).toContain("SIL OPEN FONT LICENSE Version 1.1");
  expect(text).toContain("Copyright (c) 2019 TradingView, Inc"); // fancy-canvas, from upstream
});

function fakeRoot(pkg: Record<string, unknown>, withText: boolean): string {
  const root = mkdtempSync(join(tmpdir(), "notices-"));
  const dir = join(root, "node_modules", "x");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify({ name: "x", version: "1.0.0", ...pkg }));
  if (withText) writeFileSync(join(dir, "LICENSE"), "text");
  const lock = { packages: { "": {}, "node_modules/x": { version: "1.0.0" } } };
  writeFileSync(join(root, "package-lock.json"), JSON.stringify(lock));
  return root;
}

it("refuses a shipped package that is not permissively licensed or has no text", () => {
  expect(() => collect(fakeRoot({ license: "GPL-3.0" }, true))).toThrow(/not permitted/);
  expect(() => collect(fakeRoot({ license: "MIT" }, false))).toThrow(/no licence text/);
  expect(collect(fakeRoot({ license: "MIT" }, true))).toHaveLength(1);
});
