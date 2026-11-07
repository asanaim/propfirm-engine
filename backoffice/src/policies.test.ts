import { describe, expect, it } from "vitest";
import { screensFor } from "./screens";

describe("policy pages screen (rule AC10)", () => {
  it("is offered to those who may change settings, not to readers", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("policies")).toBe(true);
    expect(screensFor(["settings.view"]).has("policies")).toBe(false);
    expect(screensFor(["staff.view"]).has("policies")).toBe(false);
  });
});
