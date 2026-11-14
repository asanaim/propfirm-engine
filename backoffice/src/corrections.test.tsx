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

describe("finding a price number and the class", () => {
  it("lists fills newest first", async () => {
    const { fillsOf } = await import("./Corrections");
    const list = fillsOf([
      { type: "PositionOpened", ts: 1, symbol: "EUR/USD", price_seq: 5 },
      { type: "DayStarted", ts: 2, symbol: "", price_seq: 0 },
      { type: "PositionClosed", ts: 3, symbol: "EUR/USD", price_seq: 9 },
    ]);
    expect(list.map((f) => f.price_seq)).toEqual([9, 5]);
  });
  it("says what a class means", async () => {
    const { classText } = await import("./ClassControl");
    expect(classText({ class: "C", reason: "short holds: 62.50% of profit", since: 0 })).toBe("Class C, every payout reviewed (short holds: 62.50% of profit)");
    expect(classText(null)).toBe("Not classed yet");
  });
});

describe("scaling", () => {
  it("shows dollars from cents", async () => {
    const { usd } = await import("./Scaling");
    expect(usd(200_000)).toBe("$2,000.00");
  });
});
