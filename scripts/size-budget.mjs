// The first download of a built web app, against its budget (kept light,
// 12 Nov 2026): the scripts its index.html loads (entry and preloads),
// gzip-compressed as Caddy serves them. Fails the build when over.
//   node ../scripts/size-budget.mjs dist 140000
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const [dist, limitText] = process.argv.slice(2);
const limit = Number(limitText);
if (!dist || !Number.isInteger(limit) || limit <= 0) {
  console.error("usage: size-budget.mjs <dist> <bytes>");
  process.exit(2);
}
const html = readFileSync(join(dist, "index.html"), "utf8");
const scripts = [...new Set([...html.matchAll(/(?:src|href)="\/?(assets\/[^"]+\.js)"/g)].map((m) => m[1]))];
let total = 0;
for (const file of scripts) {
  const size = gzipSync(readFileSync(join(dist, file))).length;
  total += size;
  console.log(`${String(size).padStart(8)}  ${file}`);
}
console.log(`${String(total).padStart(8)}  first download (gzip), budget ${limit}`);
if (scripts.length === 0 || total > limit) {
  console.error(scripts.length === 0 ? "no scripts found in index.html" : "over budget");
  process.exit(1);
}
