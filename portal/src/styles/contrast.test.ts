import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** WCAG 2.2 contrast of the design tokens in both themes. */
const css = readFileSync(resolve(__dirname, "portal.css"), "utf8");

function tokens(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const block = css.slice(start, css.indexOf("}", start));
  const found: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})/g)) {
    if (m[1] !== undefined && m[2] !== undefined) found[m[1]] = m[2];
  }
  return found;
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT: [string, string][] = [
  ["text", "bg"],
  ["text", "surface"],
  ["muted", "bg"],
  ["muted", "surface"],
  ["accent", "surface"],
  ["accent", "bg"],
  ["on-accent", "accent"],
  ["on-accent", "accent-hover"],
  ["danger", "danger-bg"],
  ["ok", "ok-bg"],
  ["text", "info-bg"],
];
// non-text (1.4.11): focus ring and input borders against the surface
const NON_TEXT: [string, string][] = [
  ["focus", "surface"],
  ["focus", "bg"],
  ["muted", "surface"],
];

describe.each([
  ["light", ":root"],
  ["dark", ':root[data-theme="dark"]'],
])("%s theme", (_name, selector) => {
  const t = tokens(selector);
  it.each(TEXT)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(contrast(t[fg] ?? "", t[bg] ?? "")).toBeGreaterThanOrEqual(4.5);
  });
  it.each(NON_TEXT)("%s against %s is at least 3:1", (fg, bg) => {
    expect(contrast(t[fg] ?? "", t[bg] ?? "")).toBeGreaterThanOrEqual(3);
  });
});

it("measures contrast as WCAG defines it", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
});
