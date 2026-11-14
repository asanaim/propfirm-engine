import { describe, expect, it } from "vitest";
import { cents, fromPoints, toPoints } from "./Corrections";

describe("price corrections", () => {
  it("reads and shows prices at the symbol's digits", () => {
    expect(toPoints("1.103", 5)).toBe(110_300);
    expect(toPoints("2650.5", 2)).toBe(265_050);
    expect(toPoints("1.123456", 5)).toBeNull();
    expect(toPoints("abc", 5)).toBeNull();
    expect(fromPoints(110_300, 5)).toBe("1.10300");
    expect(fromPoints(5, 2)).toBe("0.05");
  });
  it("signs the correction amounts", () => {
    expect([cents(60_000), cents(-1_234), cents(0)]).toEqual(["+$600.00", "-$12.34", "$0.00"]);
  });
});
