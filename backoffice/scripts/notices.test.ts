import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { choose, collect, render } from "./third-party-notices.mjs";

it("collects the texts of every shipped package", () => {
  const shipped = collect(resolve(__dirname, ".."));
  const names = shipped.map((p) => p.name);
  for (const name of ["react", "react-dom", "react-admin", "@mui/material", "oidc-client-ts", "css-mediaquery", "dompurify"]) {
    expect(names).toContain(name);
  }
  expect(names.some((n) => n.startsWith("@types/") || n === "csstype" || n === "vite")).toBe(false);
  const found = shipped.find((p) => p.name === "css-mediaquery");
  expect(found?.license).toBe("BSD-3-Clause"); // read by hand: the LICENSE is BSD-3-Clause
  expect(render(shipped)).toContain("Copyright 2014 Yahoo! Inc.");
});

it("takes a permissive alternative of an OR licence, never of an AND", () => {
  expect(choose("(MPL-2.0 OR Apache-2.0)")).toBe("Apache-2.0");
  expect(choose("MIT")).toBe("MIT");
  expect(choose("(GPL-3.0 OR MPL-2.0)")).toBe("(GPL-3.0 OR MPL-2.0)");
  expect(choose("(MIT AND GPL-3.0)")).toBe("(MIT AND GPL-3.0)");
});

function fakeRoot(pkg: Record<string, unknown>, text: string | null): string {
  const root = mkdtempSync(join(tmpdir(), "notices-"));
  const dir = join(root, "node_modules", "x");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "package.json"), JSON.stringify({ name: "x", version: "1.0.0", ...pkg }));
  if (text !== null) writeFileSync(join(dir, text), "text");
  const lock = { packages: { "": {}, "node_modules/x": { version: "1.0.0" } } };
  writeFileSync(join(root, "package-lock.json"), JSON.stringify(lock));
  return root;
}

it("refuses a shipped package that is not permissively licensed or has no text", () => {
  expect(() => collect(fakeRoot({ license: "GPL-3.0" }, "LICENSE"))).toThrow(/not permitted/);
  expect(() => collect(fakeRoot({ license: "BSD" }, "LICENSE"))).toThrow(/not permitted/); // not reviewed
  expect(() => collect(fakeRoot({ license: "MIT" }, null))).toThrow(/no licence text/);
  expect(collect(fakeRoot({ license: "MIT" }, "LICENSE-MIT.txt"))).toHaveLength(1);
});
