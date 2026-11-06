// Builds public/third-party-notices.txt: the licence and notice texts of
// every package shipped to the browser, as their licences ask (MIT, ISC and
// OFL: the copyright and licence with every copy; Apache-2.0: the NOTICE
// file). Fails the build when a shipped package's licence is not
// permissive (outline: anything traders use is permissively licensed) or
// its text cannot be found. Texts missing from an npm package are kept in
// notices/<name>-<version>.<KIND>, copied from the upstream release.
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const PERMISSIVE = new Set(["MIT", "ISC", "Apache-2.0", "OFL-1.1", "BSD-2-Clause", "BSD-3-Clause", "0BSD"]);
// packages whose package.json names no SPDX id, read by hand from their
// LICENSE text; pinned to the version, so an upgrade is read again
export const REVIEWED = new Map([["css-mediaquery@0.1.2", "BSD-3-Clause"]]); // the LICENSE is BSD-3-Clause word for word
// type declarations only: never shipped to the browser
const NOT_SHIPPED = (name) => name.startsWith("@types/") || name === "csstype";
const TEXT = /^(licen[cs]e|notice|copying)([.-]|$)/i; // LICENSE, LICENSE.md, LICENSE-MIT.txt

/** "(MPL-2.0 OR Apache-2.0)": we take the first permissive alternative
(only OR; an AND expression is refused as a whole). */
export function choose(license) {
  const inner = license.replace(/^\((.*)\)$/, "$1");
  if (!inner.includes(" OR ") || inner.includes(" AND ")) return license;
  return inner.split(" OR ").map((x) => x.trim()).find((x) => PERMISSIVE.has(x)) ?? license;
}

export function collect(root) {
  const lock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8"));
  const vendored = existsSync(join(root, "notices")) ? readdirSync(join(root, "notices")) : [];
  const out = [];
  for (const [path, entry] of Object.entries(lock.packages)) {
    if (path === "" || entry.dev === true) continue;
    const dir = join(root, path);
    const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
    if (NOT_SHIPPED(pkg.name)) continue;
    const declared = typeof pkg.license === "string" ? pkg.license : "UNKNOWN";
    const license = choose(REVIEWED.get(`${pkg.name}@${pkg.version}`) ?? declared);
    if (!PERMISSIVE.has(license)) throw new Error(`${pkg.name}@${pkg.version}: licence ${license} is not permitted`);
    const prefix = `${pkg.name.replace("/", "__")}-${pkg.version}.`;
    const texts = [
      ...readdirSync(dir).filter((f) => TEXT.test(f)).map((f) => readFileSync(join(dir, f), "utf8")),
      ...vendored.filter((f) => f.startsWith(prefix)).map((f) => readFileSync(join(root, "notices", f), "utf8")),
    ];
    if (texts.length === 0) throw new Error(`${pkg.name}@${pkg.version}: no licence text; add notices/${prefix}LICENSE`);
    out.push({ name: pkg.name, version: pkg.version, license, texts });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export function render(packages) {
  const parts = [
    "Third-party software in this back office, with the licence and notice texts its",
    "licences ask to be shown. Generated at build from the installed packages.",
    "",
  ];
  for (const p of packages) {
    parts.push("=".repeat(78), `${p.name} ${p.version} (${p.license})`, "=".repeat(78), "");
    for (const text of p.texts) parts.push(text.trim(), "");
  }
  return `${parts.join("\n")}\n`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const packages = collect(root);
  writeFileSync(join(root, "public", "third-party-notices.txt"), render(packages));
  console.log(`third-party notices: ${packages.length} packages`);
}
