import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { Market } from "../api/types";
import { Problem } from "../components/Problem";
import { Card, Loading, ScrollRegion } from "../components/ui";
import { formatCents, formatLots, formatPrice } from "../lib/money";
import { Link } from "../router";
import { AccountTabs } from "./AccountPage";

/** One record of the account's journal history (GET /accounts/{acc}/history). */
export interface HistoryEvent {
  seq: number;
  idx: number;
  type: string;
  ts: number;
  [field: string]: unknown;
}

export interface Trade {
  ts: number;
  position_id: string;
  symbol: string;
  side: string;
  volume: number;
  open_price: number;
  close_price: number;
  pnl_cents: number;
  reason: string;
}

const PAGE = 1_000;

/** Every event of the account, oldest first, page by page. */
export async function loadHistory(get: <T>(path: string) => Promise<T>, acc: string): Promise<HistoryEvent[]> {
  const out: HistoryEvent[] = [];
  let after = 0;
  for (;;) {
    const page = await get<HistoryEvent[]>(`/accounts/${acc}/history?after=${after}&limit=${PAGE}`);
    out.push(...page);
    const last = page.at(-1);
    if (page.length < PAGE || last === undefined) return out;
    after = last.seq;
  }
}

export function tradesOf(events: HistoryEvent[]): Trade[] {
  return events
    .filter((e) => e.type === "PositionClosed")
    .map((e) => ({
      ts: e.ts,
      position_id: String(e.position_id),
      symbol: String(e.symbol),
      side: String(e.side),
      volume: Number(e.volume),
      open_price: Number(e.open_price),
      close_price: Number(e.close_price),
      pnl_cents: Number(e.pnl_cents),
      reason: String(e.reason),
    }));
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Closed trades as CSV: exact values (prices with the symbol's digits,
money in dollars with cents), times in UTC. */
export function tradesCsv(trades: Trade[], digitsOf: (symbol: string) => number): string {
  const head = ["closed_utc", "position", "symbol", "side", "lots", "open_price", "close_price", "profit_usd", "reason"];
  const rows = trades.map((t) => {
    const digits = digitsOf(t.symbol);
    return [
      new Date(t.ts).toISOString(),
      t.position_id,
      t.symbol,
      t.side,
      formatLots(t.volume),
      formatPrice(t.open_price, digits),
      formatPrice(t.close_price, digits),
      formatCents(t.pnl_cents).replace(/[$,]/g, ""),
      t.reason,
    ];
  });
  return `${[head, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n")}\r\n`;
}

const REASONS: Record<string, string> = {
  client: "You closed it",
  stop_loss: "Stop loss",
  take_profit: "Take profit",
  trailing_stop: "Trailing stop",
  breach: "Rule breach",
  void: "Account closed",
};

export function HistoryPage({ acc }: { acc: string }) {
  const api = useApi();
  const [events, setEvents] = useState<HistoryEvent[] | null>(null);
  const [market, setMarket] = useState<Market | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    loadHistory(api.get, acc).then(setEvents, setError);
    api.get<Market>("/market").then(setMarket, setError);
  }, [api, acc]);
  if (error !== null) return <Problem error={error} />;
  if (events === null || market === null) return <Loading />;
  const digitsOf = (symbol: string) => market.symbols.find((s) => s.symbol === symbol)?.digits ?? 5;
  const trades = tradesOf(events).reverse(); // newest first on screen
  const total = trades.reduce((sum, t) => sum + t.pnl_cents, 0);
  const commission = events
    .filter((e) => e.type === "CommissionCharged")
    .reduce((sum, e) => sum + Number(e.commission_cents), 0);
  const swaps = events.filter((e) => e.type === "SwapCharged").reduce((sum, e) => sum + Number(e.swap_cents), 0);
  const download = () => {
    const blob = new Blob([tradesCsv([...trades].reverse(), digitsOf)], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${acc}-trades.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return (
    <div className="page">
      <p className="crumbs">
        <Link href="/accounts">Accounts</Link> / {acc}
      </p>
      <AccountTabs acc={acc} current="history" />
      <h1>History</h1>
      <Card
        title="Closed trades"
        actions={
          <button type="button" className="button button-secondary" onClick={download} disabled={trades.length === 0}>
            <Download aria-hidden="true" /> Export CSV
          </button>
        }
      >
        <p>
          {trades.length} trades: {formatCents(total)} profit, {formatCents(commission)} commission,{" "}
          {formatCents(swaps)} overnight swaps.
        </p>
      </Card>
      {trades.length > 0 && (
        <ScrollRegion label="Closed trades">
          <table>
            <caption className="visually-hidden">Closed trades</caption>
            <thead>
              <tr>
                <th scope="col">Closed</th>
                <th scope="col">Symbol</th>
                <th scope="col">Side</th>
                <th scope="col">Lots</th>
                <th scope="col">Open</th>
                <th scope="col">Close</th>
                <th scope="col">Profit</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const digits = digitsOf(t.symbol);
                return (
                  <tr key={`${t.position_id}-${t.ts}`}>
                    <td>{new Date(t.ts).toLocaleString()}</td>
                    <th scope="row">{t.symbol}</th>
                    <td>{t.side === "buy" ? "Buy" : "Sell"}</td>
                    <td>{formatLots(t.volume)}</td>
                    <td>{formatPrice(t.open_price, digits)}</td>
                    <td>{formatPrice(t.close_price, digits)}</td>
                    <td className={t.pnl_cents < 0 ? "loss" : undefined}>{formatCents(t.pnl_cents)}</td>
                    <td>{REASONS[t.reason] ?? t.reason}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollRegion>
      )}
      <h2>Account record</h2>
      <p className="muted">Every event of the account, newest first, as the engine recorded it.</p>
      <ScrollRegion label="Account record">
        <table>
          <caption className="visually-hidden">Account record</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Event</th>
              <th scope="col">Record</th>
            </tr>
          </thead>
          <tbody>
            {[...events].reverse().map((e) => (
              <tr key={`${e.seq}-${e.idx}`}>
                <td>{new Date(e.ts).toLocaleString()}</td>
                <th scope="row">{e.type}</th>
                <td>{e.seq}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollRegion>
    </div>
  );
}
