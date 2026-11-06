import { money, when } from "./format";

describe("money", () => {
  it("shows integer cents as dollars without rounding", () => {
    expect(money(123456)).toBe("1,234.56 USD");
    expect(money(5)).toBe("0.05 USD");
    expect(money(-100)).toBe("-1.00 USD");
    expect(money(0)).toBe("0.00 USD");
    expect(money(null)).toBe("-");
  });
  it("refuses a fraction of a cent", () => {
    expect(() => money(1.5)).toThrow("not integer cents");
  });
});

describe("when", () => {
  it("shows epoch milliseconds in UTC", () => {
    expect(when(Date.UTC(2026, 10, 6, 9, 30))).toBe("2026-11-06 09:30 UTC");
    expect(when(undefined)).toBe("-");
  });
});
