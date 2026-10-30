import { ordinal } from "../pages/ShopPage";
import { formatCents, formatDuration, formatLots, formatPercent, formatPrice, formatSize } from "./money";

it("formats integer cents as dollars without floating point", () => {
  expect(formatCents(0)).toBe("$0.00");
  expect(formatCents(5)).toBe("$0.05");
  expect(formatCents(4_400)).toBe("$44.00");
  expect(formatCents(123_456_789)).toBe("$1,234,567.89");
  expect(formatCents(-1_050)).toBe("-$10.50");
  expect(formatCents(Number.MAX_SAFE_INTEGER)).toBe("$90,071,992,547,409.91");
  expect(() => formatCents(1.5)).toThrow(RangeError);
  expect(() => formatCents(Number.MAX_SAFE_INTEGER + 1)).toThrow(RangeError);
});

it("shows round sizes in thousands", () => {
  expect(formatSize(500_000)).toBe("$5K");
  expect(formatSize(20_000_000)).toBe("$200K");
  expect(formatSize(150_000)).toBe("$1,500.00");
  expect(formatSize(1_000_050)).toBe("$10,000.50");
  expect(() => formatSize(-1)).toThrow(RangeError);
});

it("keeps exact percent text", () => {
  expect(formatPercent("4.5")).toBe("4.5 %");
  expect(formatPercent(null)).toBe("none");
});

it("names payouts in order", () => {
  expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 103, 111].map(ordinal)).toEqual([
    "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "103rd", "111th",
  ]);
});

it("shows prices from integer points with the symbol's digits", () => {
  expect(formatPrice(110_005, 5)).toBe("1.10005");
  expect(formatPrice(5, 5)).toBe("0.00005");
  expect(formatPrice(15_015, 3)).toBe("15.015");
  expect(formatPrice(8_090_545, 2)).toBe("80905.45");
  expect(formatPrice(42, 0)).toBe("42");
  expect(formatPrice(-150, 2)).toBe("-1.50");
  expect(() => formatPrice(1.5, 2)).toThrow(RangeError);
  expect(formatLots(150)).toBe("1.50");
  expect(formatLots(1)).toBe("0.01");
});

it("counts down in hours and minutes", () => {
  expect(formatDuration(0)).toBe("0 min");
  expect(formatDuration(-5_000)).toBe("0 min");
  expect(formatDuration(59_999)).toBe("0 min");
  expect(formatDuration(7 * 60_000)).toBe("7 min");
  expect(formatDuration(3 * 3_600_000 + 7 * 60_000 + 59_000)).toBe("3 h 07 min");
});
