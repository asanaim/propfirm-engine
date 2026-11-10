/**
 * The public trading calculators (position size, pip value, margin, profit).
 * Account currency is US dollars. These are estimates for planning trades:
 * the platform's own figures (fills, margin, P/L) come from the engine.
 * Rounding is explicit: lot sizes round down to 0.01 lot (never above the
 * risk asked for); money shows to the cent.
 */

export interface ToolInstrument {
  symbol: string;
  asset_class: string;
  digits: number;
  contract_size: number;
  leverage: string; // "100/1"
  quote_currency: string;
  pip: string; // "0.0001"
  pip_name: "pip" | "point";
  commission_percent: string; // "0", or e.g. "0.0525"
}

export function leverageOf(spec: ToolInstrument): number {
  const [n, d] = spec.leverage.split("/").map(Number);
  return (n ?? 1) / (d ?? 1);
}

/** How many US dollars one unit of the quote currency is worth. For a
USD/XXX pair it follows from the price itself; for other quotes (EUR/GBP:
GBP) the trader gives the rate. Null when it cannot be known. */
export function usdPerQuote(spec: ToolInstrument, price: number | null, rate: number | null): number | null {
  if (spec.quote_currency === "USD") return 1;
  if (spec.symbol.startsWith("USD/") && price !== null && price > 0) return 1 / price;
  return rate !== null && rate > 0 ? rate : null;
}

/** US dollars that one pip (or point) is worth on `lots` lots. */
export function pipValue(spec: ToolInstrument, lots: number, usd: number): number {
  return Number(spec.pip) * spec.contract_size * lots * usd;
}

/** The largest lot size, in steps of 0.01, that risks at most `riskPercent`
of `balance` when the stop is `stop` pips (or points) away. */
export function positionSize(balance: number, riskPercent: number, stop: number, spec: ToolInstrument, usd: number): number {
  const perLot = stop * pipValue(spec, 1, usd);
  if (!(perLot > 0) || !(balance > 0) || !(riskPercent > 0)) return 0;
  const lots = (balance * riskPercent) / 100 / perLot;
  return Math.floor(lots * 100 + 1e-9) / 100; // round down to 0.01 lot
}

/** Margin in US dollars to open `lots` lots at `price`. */
export function margin(spec: ToolInstrument, lots: number, price: number, usd: number): number {
  return (lots * spec.contract_size * price * usd) / leverageOf(spec);
}

/** Profit (negative: loss) in US dollars from `open` to `close`. */
export function profit(spec: ToolInstrument, side: "buy" | "sell", lots: number, open: number, close: number, usd: number): number {
  const move = side === "buy" ? close - open : open - close;
  return move * spec.contract_size * lots * usd;
}

/** A number typed by the trader, or null when blank or not a number. */
export function parse(text: string): number | null {
  const v = Number(text.replace(/,/g, "").trim());
  return text.trim() === "" || !Number.isFinite(v) ? null : v;
}

export const usd = (v: number): string =>
  `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
