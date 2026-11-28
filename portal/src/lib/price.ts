/** Prices and volumes typed by the trader, read without floating point:
"1.10005" with 5 digits is 110005 points; "0.10" lots is 10 hundredths. */

export function parsePrice(text: string, digits: number): number | null {
  const t = text.trim();
  if (digits === 0) {
    const whole = /^\d+$/.test(t) ? Number(t) : 0;
    return Number.isSafeInteger(whole) && whole > 0 ? whole : null;
  }
  const match = new RegExp(`^(\\d+)(?:\\.(\\d{1,${String(digits)}}))?$`).exec(t);
  if (match === null) return null;
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(digits, "0");
  const points = Number(`${whole}${fraction}`);
  return Number.isSafeInteger(points) && points > 0 ? points : null;
}

/** Whole points, at least 1 (trailing distance and step). */
export function parsePoints(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const points = Number(t);
  return Number.isSafeInteger(points) && points >= 1 ? points : null;
}

export function parseLots(text: string): number | null {
  const volume = parsePrice(text, 2);
  return volume !== null && volume >= 1 ? volume : null;
}

/** The engine's reasons for refusing an order, in words. */
export const REJECTIONS: Record<string, string> = {
  unknown_account: "This account cannot trade.",
  unknown_symbol: "This symbol is not traded.",
  duplicate_id: "This order was already sent.",
  symbol_frozen: "No current price for this symbol: trading resumes with the next price.",
  invalid_volume: "The volume is not allowed for this symbol.",
  order_volume_cap: "The order is larger than one order may be.",
  symbol_volume_cap: "This would exceed the most you may hold in this symbol.",
  invalid_level: "The price level is on the wrong side of the market for this order type.",
  invalid_stops: "The stop loss or take profit is on the wrong side of the price.",
  insufficient_margin: "Not enough free margin for this order.",
  no_conversion_rate: "No conversion price is available yet for this symbol.",
  unknown_position: "This position is already closed.",
  unknown_order: "This order is no longer pending.",
  account_locked: "This account is closed for trading.",
  news_window: "No trades open or close around a listed news event (see the news calendar).",
  invalid_trailing: "Distance and step must be whole points, at least 1, and the level must stay above zero.",
  position_closed: "The position closed, so its trailing stop was cancelled.",
  market_break:
    "The market closes for 2 hours or more in a few minutes, so funded accounts open no new trades until it reopens.",
  market_closed: "The market for this symbol is closed now. Orders, stops and cancellations wait for it to open.",
  stale_quote: "The last price is a few seconds old, so the order was not filled on it. Try again on the next price.",
  risk_limit: "Lot limits on this symbol and side are reduced for now. Try a smaller volume.",
  close_only: "This account may close positions but not open new ones for now. Contact support for details.",
};

export function rejection(reason: unknown): string {
  return typeof reason === "string" ? (REJECTIONS[reason] ?? `Refused: ${reason}.`) : "Refused.";
}
