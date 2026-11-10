import { describe, expect, it } from "vitest";
import { margin, parse, pipValue, positionSize, profit, type ToolInstrument, usdPerQuote } from "./calculators";

const EURUSD: ToolInstrument = { symbol: "EUR/USD", asset_class: "forex", digits: 5, contract_size: 100_000, leverage: "100/1", quote_currency: "USD", pip: "0.0001", pip_name: "pip" };
const USDJPY: ToolInstrument = { ...EURUSD, symbol: "USD/JPY", digits: 3, quote_currency: "JPY", pip: "0.01" };
const EURGBP: ToolInstrument = { ...EURUSD, symbol: "EUR/GBP", quote_currency: "GBP" };
const XAUUSD: ToolInstrument = { symbol: "XAU/USD", asset_class: "metal", digits: 2, contract_size: 100, leverage: "50/1", quote_currency: "USD", pip: "0.01", pip_name: "point" };

describe("trading calculators", () => {
  it("prices a pip as the market does", () => {
    expect(pipValue(EURUSD, 1, 1)).toBeCloseTo(10, 10); // $10 a pip on a standard lot
    expect(pipValue(USDJPY, 1, usdPerQuote(USDJPY, 150, null) ?? 0)).toBeCloseTo(6.6667, 4);
    expect(pipValue(XAUUSD, 1, 1)).toBeCloseTo(1, 10); // $1 a point on 100 oz
    expect(usdPerQuote(EURGBP, 0.86, null)).toBeNull(); // GBP->USD must be given
    expect(pipValue(EURGBP, 1, usdPerQuote(EURGBP, 0.86, 1.27) ?? 0)).toBeCloseTo(12.7, 10);
  });

  it("sizes a position to the risk, rounding down to 0.01 lot", () => {
    expect(positionSize(100_000, 1, 20, EURUSD, 1)).toBe(5); // $1,000 / (20 x $10)
    expect(positionSize(10_000, 1, 30, EURUSD, 1)).toBe(0.33); // 0.3333 rounds down
    expect(positionSize(10_000, 0, 30, EURUSD, 1)).toBe(0);
    expect(positionSize(10_000, 1, 0, EURUSD, 1)).toBe(0);
  });

  it("works out margin and profit", () => {
    expect(margin(EURUSD, 1, 1.1, 1)).toBeCloseTo(1_100, 6); // $110,000 at 1:100
    expect(margin(USDJPY, 1, 150, 1 / 150)).toBeCloseTo(1_000, 6);
    expect(margin(XAUUSD, 1, 2_400, 1)).toBeCloseTo(4_800, 6);
    expect(profit(EURUSD, "buy", 1, 1.1, 1.1025, 1)).toBeCloseTo(250, 6);
    expect(profit(EURUSD, "sell", 1, 1.1, 1.1025, 1)).toBeCloseTo(-250, 6);
  });

  it("reads typed numbers", () => {
    expect(parse("100,000")).toBe(100_000);
    expect(parse(" ")).toBeNull();
    expect(parse("abc")).toBeNull();
  });
});
