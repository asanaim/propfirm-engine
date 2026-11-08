import { describe, expect, it } from "vitest";
import { screensFor } from "./screens";

describe("policy pages screen (rule AC10)", () => {
  it("is offered to those who may change settings, not to readers", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("policies")).toBe(true);
    expect(screensFor(["settings.view"]).has("policies")).toBe(false);
    expect(screensFor(["staff.view"]).has("policies")).toBe(false);
  });
});

describe("website editor (rules AC07, AC08)", () => {
  it("is offered to those who may change settings", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("site")).toBe(true);
    expect(screensFor(["settings.view"]).has("site")).toBe(false);
  });

  it("previews each token as the site will fill it, leaving unknown ones", async () => {
    const { preview } = await import("./Site");
    expect(preview("Keep {max_split}, from {min_price} {nope}", { max_split: "90%", min_price: "$59" })).toBe("Keep 90%, from $59 {nope}");
  });
});

describe("payment methods screen (rules AC13, AC14)", () => {
  it("is offered to those who may change settings", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("methods")).toBe(true);
    expect(screensFor(["settings.view"]).has("methods")).toBe(false);
  });

  it("reads dollars as whole cents, refusing anything else", async () => {
    const { toCents } = await import("./PaymentMethods");
    expect(toCents("122")).toBe(12_200);
    expect(toCents("1.5")).toBe(150);
    expect(toCents("0.01")).toBe(1);
    expect(toCents("1.234")).toBeNull();
    expect(toCents("-1")).toBeNull();
    expect(toCents("abc")).toBeNull();
  });
});
