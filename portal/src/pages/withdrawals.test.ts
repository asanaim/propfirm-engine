import { describe, expect, it } from "vitest";
import { methodAllows, methodCharge } from "./PayoutsPage";
import type { PayMethod } from "./ShopPage";

describe("withdrawal methods at request time (D-14.25)", () => {
  it("charges as the server does: fixed plus percent, rounded up to a cent", () => {
    expect(methodCharge(80_000, 100, "1")).toBe(900);
    expect(methodCharge(80_001, 100, "1")).toBe(901); // 800.01 -> 801
    expect(methodCharge(12_345, 0, "0.5")).toBe(62); // 61.725 -> 62
    expect(methodCharge(12_345, 0, "0")).toBe(0);
    expect(methodCharge(10_000, 250, "")).toBe(250);
  });

  it("checks the method's limits, a maximum of 0 being none", () => {
    const m: PayMethod = { method_id: "usdt", name: "USDT", min_cents: 1_000, max_cents: 0, fixed_charge_cents: 0, percent_charge: "0", instructions: "" };
    expect(methodAllows(m, 999)).toBe(false);
    expect(methodAllows(m, 10 ** 9)).toBe(true);
    expect(methodAllows({ ...m, max_cents: 5_000 }, 5_001)).toBe(false);
  });
});

describe("payout networks (D-14.26)", () => {
  it("checks the shape of an address on its own network", async () => {
    const { looksLike } = await import("./PayoutsPage");
    expect(looksLike("TRC20", "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8")).toBe(true);
    expect(looksLike("BEP20", "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8")).toBe(false);
    expect(looksLike("ERC20", "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")).toBe(true);
    expect(looksLike("TRC20", "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")).toBe(false);
  });
});
