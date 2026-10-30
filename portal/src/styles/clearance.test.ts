import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

/** Design clearance (D44): Apple-inspired, nothing of Apple's or another
vendor's — no names, fonts, icon sets or signature colours in what the
portal ships. Fonts and icons are open-licence (Inter, Lucide). */
const FORBIDDEN: [RegExp, string][] = [
  [/\bapple\b/i, "the Apple name"],
  [/-apple-system|blinkmacsystemfont|system-ui/i, "a platform system font (San Francisco on Apple devices)"],
  [/\bsf (pro|compact|mono|symbols)\b|san francisco/i, "an Apple font or icon set"],
  [/\b(segoe|helvetica|roboto)\b/i, "another vendor's font"],
  [/#(007aff|0a84ff|34c759|ff3b30)\b/i, "an Apple system colour"],
  [/\b(cupertino|iphone|ipad|macos|ios)\b/i, "an Apple product name"],
];

const root = resolve(__dirname, "../..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(tsx?|css|html)$/.test(name) && !name.endsWith(".test.ts") ? [path] : [];
  });
}

it.each(FORBIDDEN)("ships nothing matching %s (%s)", (pattern) => {
  const shipped = [...files(join(root, "src")), join(root, "index.html")];
  const found = shipped.filter((path) => pattern.test(readFileSync(path, "utf8")));
  expect(found).toEqual([]);
});

it("depends only on the open-licence font and icons", () => {
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8")) as { dependencies: Record<string, string> };
  const fonts = Object.keys(pkg.dependencies).filter((d) => /font|icon/.test(d));
  expect(fonts.sort()).toEqual(["@fontsource-variable/inter"]);
  expect(Object.keys(pkg.dependencies)).toContain("lucide-react");
});
