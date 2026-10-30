import { useState, type SyntheticEvent } from "react";
import { useApi } from "../api/ApiProvider";
import type { Market, Quote } from "../api/types";
import { formatLots, formatPrice } from "../lib/money";
import { parseLots, parsePrice, rejection } from "../lib/price";
import { Problem } from "./Problem";
import { Button, Card, Notice } from "./ui";

export interface Outcome {
  status: "done" | "rejected" | "pending";
  events?: { type: string; reason?: string; price?: number; level?: number }[];
}

/** What the engine did with an input, in words. */
export function describeOutcome(outcome: Outcome, digits: number): { tone: "ok" | "error" | "info"; text: string } {
  if (outcome.status === "pending") return { tone: "info", text: "Sent; the engine has not answered yet." };
  const events = outcome.events ?? [];
  const refused = events.find((e) => e.type === "OrderRejected" || e.type === "InputRejected");
  if (outcome.status === "rejected" || refused !== undefined) return { tone: "error", text: rejection(refused?.reason) };
  const opened = events.find((e) => e.type === "PositionOpened");
  if (opened?.price !== undefined) return { tone: "ok", text: `Filled at ${formatPrice(opened.price, digits)}.` };
  const placed = events.find((e) => e.type === "OrderPlaced");
  if (placed?.level !== undefined) return { tone: "ok", text: `Order placed at ${formatPrice(placed.level, digits)}.` };
  if (events.some((e) => e.type === "PositionClosed")) return { tone: "ok", text: "Closed." };
  if (events.some((e) => e.type === "StopsModified")) return { tone: "ok", text: "Stops changed." };
  if (events.some((e) => e.type === "OrderCancelled")) return { tone: "ok", text: "Order cancelled." };
  return { tone: "ok", text: "Done." };
}

type Kind = "market" | "limit" | "stop";

/** Market, limit and stop orders (M09) with optional stop loss and take
profit; prices are typed as decimals and sent as integer points. */
export function OrderTicket({
  acc,
  market,
  quotes,
  disabled,
}: {
  acc: string;
  market: Market;
  quotes: Record<string, Quote>;
  disabled: boolean;
}) {
  const api = useApi();
  const [symbol, setSymbol] = useState(market.symbols[0]?.symbol ?? "EUR/USD");
  const [type, setType] = useState<Kind>("market");
  const [lots, setLots] = useState("0.10");
  const [level, setLevel] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [takeProfit, setTakeProfit] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "error" | "info"; text: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const digits = market.symbols.find((s) => s.symbol === symbol)?.digits ?? 5;
  const quote = quotes[symbol];

  const volume = parseLots(lots);
  const levelPoints = type === "market" ? null : parsePrice(level, digits);
  const sl = stopLoss.trim() === "" ? null : parsePrice(stopLoss, digits);
  const tp = takeProfit.trim() === "" ? null : parsePrice(takeProfit, digits);
  const problems = [
    volume === null && "Volume: lots in steps of 0.01, at least 0.01.",
    type !== "market" && levelPoints === null && `Price: a positive number with up to ${digits} decimals.`,
    stopLoss.trim() !== "" && sl === null && `Stop loss: a positive number with up to ${digits} decimals.`,
    takeProfit.trim() !== "" && tp === null && `Take profit: a positive number with up to ${digits} decimals.`,
  ].filter((p): p is string => typeof p === "string");

  const send = async (side: "buy" | "sell", event: SyntheticEvent) => {
    event.preventDefault();
    if (problems.length || volume === null) return;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const body = {
        symbol,
        side,
        type,
        volume,
        ...(levelPoints === null ? {} : { level: levelPoints }),
        ...(sl === null ? {} : { stop_loss: sl }),
        ...(tp === null ? {} : { take_profit: tp }),
      };
      const outcome = await api.post<Outcome>(`/accounts/${acc}/orders`, body);
      setResult(describeOutcome(outcome, digits));
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const verb = type === "market" ? "at market" : type === "limit" ? "limit" : "stop";
  const size = volume === null ? lots : formatLots(volume);
  return (
    <Card title="New order">
      <form className="form ticket" onSubmit={(e) => void send("buy", e)}>
        <div className="ticket-row">
          <label className="field">
            <span>Symbol</span>
            <select value={symbol} onChange={(e) => { setSymbol(e.target.value); }}>
              {market.symbols.map((s) => (
                <option key={s.symbol}>{s.symbol}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Order type</span>
            <select value={type} onChange={(e) => { setType(e.target.value as Kind); }}>
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop</option>
            </select>
          </label>
          <label className="field">
            <span>Volume (lots)</span>
            <input inputMode="decimal" value={lots} onChange={(e) => { setLots(e.target.value); }} />
          </label>
          {type !== "market" && (
            <label className="field">
              <span>Price</span>
              <input inputMode="decimal" value={level} onChange={(e) => { setLevel(e.target.value); }} />
            </label>
          )}
          <label className="field">
            <span>Stop loss (optional)</span>
            <input inputMode="decimal" value={stopLoss} onChange={(e) => { setStopLoss(e.target.value); }} />
          </label>
          <label className="field">
            <span>Take profit (optional)</span>
            <input inputMode="decimal" value={takeProfit} onChange={(e) => { setTakeProfit(e.target.value); }} />
          </label>
        </div>
        <p className="muted">
          {quote?.bid != null && quote.ask != null
            ? `Bid ${formatPrice(quote.bid, digits)} · Ask ${formatPrice(quote.ask, digits)}`
            : "No current price for this symbol."}
        </p>
        {problems.length > 0 && (
          <ul className="problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <div className="card-actions">
          <Button type="button" className="button button-sell" busy={busy} disabled={disabled || problems.length > 0} onClick={(e) => void send("sell", e)}>
            Sell {size} {symbol} {verb}
          </Button>
          <Button type="submit" className="button button-buy" busy={busy} disabled={disabled || problems.length > 0}>
            Buy {size} {symbol} {verb}
          </Button>
        </div>
        {result !== null && <Notice tone={result.tone}>{result.text}</Notice>}
        {error !== null && <Problem error={error} />}
      </form>
    </Card>
  );
}
