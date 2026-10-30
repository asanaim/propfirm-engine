import { AlertTriangle, Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { Attribution as AttributionSource, Bar, LiveAccount, Market, Quote } from "../api/types";
import { useAccountStream } from "../api/useAccountStream";
import { useAuth } from "../auth/AuthProvider";
import { Attribution } from "../components/Attribution";
import { PriceChart } from "../components/PriceChart";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatCents, formatDuration, formatLots, formatPrice } from "../lib/money";
import { Link } from "../router";

const STAGES: Record<string, string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };
const STATUS: Record<LiveAccount["status"], string> = {
  active: "Active",
  passed: "Passed",
  breached: "Breached",
  voided: "Closed",
};

function clamp(x: number): number {
  return Math.min(1, Math.max(0, x));
}

function Meter({ value, label, tone }: { value: number; label: string; tone: "ok" | "warn" | "danger" }) {
  return (
    <div
      className={`meter meter-${tone}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
    >
      <span style={{ width: `${(value * 100).toFixed(1)}%` }} />
    </div>
  );
}

/** Loss room left before a floor: tone by how much of the limit is used. */
function LossCard({ title, equity, floor, limit }: { title: string; equity: number; floor: number; limit: number }) {
  const left = equity - floor;
  const share = limit > 0 ? clamp(left / limit) : 0;
  const tone = share > 0.5 ? "ok" : share > 0.2 ? "warn" : "danger";
  return (
    <Card title={title}>
      <p className="figure">{formatCents(Math.max(0, left))}</p>
      <p className="muted">left of {formatCents(limit)}</p>
      <Meter value={share} label={`${title}: share of the limit left`} tone={tone} />
      <dl className="facts">
        <dt>Floor (equity)</dt>
        <dd>{formatCents(floor)}</dd>
      </dl>
    </Card>
  );
}

export function ResetCard({ account, now }: { account: LiveAccount; now: number }) {
  const atRisk = account.equity_cents <= account.next_daily_floor_cents;
  return (
    <Card title="Daily reset">
      <p className="figure">{formatDuration(account.next_reset_ts - now)}</p>
      <p className="muted">until 17:00 New York</p>
      <dl className="facts">
        <dt>Daily floor from then</dt>
        <dd>{formatCents(account.next_daily_floor_cents)}</dd>
      </dl>
      {atRisk && account.status === "active" && (
        <Notice tone="error">
          <AlertTriangle aria-hidden="true" className="inline-icon" /> If equity is still at or below{" "}
          {formatCents(account.next_daily_floor_cents)} at the reset, the daily loss limit is breached then: the new
          day starts from the higher of balance and equity.
        </Notice>
      )}
    </Card>
  );
}

function TargetCard({ account }: { account: LiveAccount }) {
  const target = account.target_balance_cents;
  if (target === null) {
    return (
      <Card title="Profit target">
        <p>No target on a funded account.</p>
      </Card>
    );
  }
  const initial = account.initial_balance_cents;
  const share = target > initial ? clamp((account.balance_cents - initial) / (target - initial)) : 0;
  return (
    <Card title="Profit target">
      <p className="figure">{formatCents(Math.max(0, target - account.balance_cents))}</p>
      <p className="muted">to go, on the closed balance (target {formatCents(target)})</p>
      <Meter value={share} label="Profit target reached" tone="ok" />
    </Card>
  );
}

function Quotes({ quotes, attribution }: { quotes: Record<string, Quote>; attribution: AttributionSource | null }) {
  const list = Object.values(quotes).sort((a, b) => a.symbol.localeCompare(b.symbol));
  return (
    <>
      <div className="table-wrap">
        <table>
          <caption className="visually-hidden">Prices</caption>
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">Bid</th>
              <th scope="col">Ask</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {list.map((q) => (
              <tr key={q.symbol}>
                <th scope="row">{q.symbol}</th>
                <td>{q.bid === null ? "—" : formatPrice(q.bid, q.digits)}</td>
                <td>{q.ask === null ? "—" : formatPrice(q.ask, q.digits)}</td>
                <td>{q.frozen ? "No price" : q.blocked ? "News window" : "Open"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Attribution source={attribution} />
    </>
  );
}

function Chart({ market, quotes }: { market: Market; quotes: Record<string, Quote> }) {
  const api = useApi();
  const [symbol, setSymbol] = useState(market.symbols[0]?.symbol ?? "EUR/USD");
  const [interval, setInterval] = useState("1m");
  const [bars, setBars] = useState<{ key: string; bars: Bar[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const key = `${symbol}|${interval}`;
  useEffect(() => {
    let alive = true;
    api.get<Bar[]>(`/bars?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=300`).then(
      (found) => {
        if (alive) setBars({ key: `${symbol}|${interval}`, bars: found });
      },
      (reason: unknown) => {
        if (alive) setError(reason);
      },
    );
    return () => {
      alive = false;
    };
  }, [api, symbol, interval]);
  const digits = market.symbols.find((s) => s.symbol === symbol)?.digits ?? 5;
  return (
    <Card title="Chart">
      <div className="row chart-controls">
        <label className="field inline">
          <span>Symbol</span>
          <select
            value={symbol}
            onChange={(e) => {
              setSymbol(e.target.value);
            }}
          >
            {market.symbols.map((s) => (
              <option key={s.symbol}>{s.symbol}</option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>Bars</span>
          <select
            value={interval}
            onChange={(e) => {
              setInterval(e.target.value);
            }}
          >
            {market.intervals.map((i) => (
              <option key={i}>{i}</option>
            ))}
          </select>
        </label>
      </div>
      {error !== null && <Problem error={error} />}
      {bars?.key === key ? (
        <PriceChart
          bars={bars.bars}
          digits={digits}
          interval={interval}
          quote={quotes[symbol]}
          label={`${symbol} ${interval} candles`}
        />
      ) : (
        <Loading label="Loading chart" />
      )}
      <Attribution source={market.attribution}>
        . Chart by{" "}
        <a href="https://www.tradingview.com/" rel="noopener" target="_blank">
          TradingView
        </a>{" "}
        Lightweight Charts (<Link href="/notices">notices</Link>).
      </Attribution>
    </Card>
  );
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 15_000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);
  return now;
}

export function AccountPage({ acc }: { acc: string }) {
  const auth = useAuth();
  const api = useApi();
  const live = useAccountStream(acc);
  const [market, setMarket] = useState<Market | null>(null);
  const now = useNow();
  useEffect(() => {
    api.get<Market>("/market").then(setMarket, () => {
      setMarket(null);
    });
  }, [api]);

  if (auth.status !== "signed_in") {
    return <Button onClick={() => void auth.signIn(`/accounts/${acc}`)}>Sign in to see this account</Button>;
  }
  if (live.error !== null) return <Problem error={live.error} />;
  const account = live.account;
  if (account === null) return <Loading label="Connecting" />;
  const floating = account.equity_cents - account.balance_cents;
  return (
    <div className="page">
      <p className="crumbs">
        <Link href="/accounts">Accounts</Link> / {acc}
      </p>
      <div className="title-row">
        <h1>{acc}</h1>
        <span className={`badge badge-${account.status}`}>{STATUS[account.status]}</span>
        <span className="muted">
          {STAGES[account.rules.stage] ?? account.rules.stage} · rules {account.rsv_id}
        </span>
        <span className={live.connected ? "live on" : "live"} role="status">
          <Radio aria-hidden="true" className="inline-icon" /> {live.connected ? "Live" : "Reconnecting"}
        </span>
      </div>
      <div className="grid">
        <Card title="Balance">
          <p className="figure">{formatCents(account.balance_cents)}</p>
          <p className="muted">started at {formatCents(account.initial_balance_cents)}</p>
        </Card>
        <Card title="Equity">
          <p className="figure">{formatCents(account.equity_cents)}</p>
          <p className={floating < 0 ? "loss" : "muted"}>
            {floating === 0 ? "no open profit or loss" : `${formatCents(floating)} open`}
          </p>
        </Card>
        <LossCard
          title="Daily loss"
          equity={account.equity_cents}
          floor={account.daily_floor_cents}
          limit={account.daily_loss_limit_cents}
        />
        <LossCard
          title="Maximum loss"
          equity={account.equity_cents}
          floor={account.max_floor_cents}
          limit={account.max_loss_limit_cents}
        />
        <TargetCard account={account} />
        <Card title="Trading days">
          <p className="figure">{account.trading_days}</p>
          <p className="muted">of {account.rules.min_trading_days} needed · no time limit</p>
          {account.rules.consistency_percent !== null && (
            <p>
              Consistency (best day at most {account.rules.consistency_percent} %):{" "}
              {account.consistency_ok ? "met" : "not met yet"}
            </p>
          )}
        </Card>
        <ResetCard account={account} now={now} />
      </div>
      {market !== null && <Chart market={market} quotes={live.quotes} />}
      <h2>Open positions</h2>
      {account.positions.length === 0 ? (
        <p className="muted">No open positions.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="visually-hidden">Open positions</caption>
            <thead>
              <tr>
                <th scope="col">Symbol</th>
                <th scope="col">Side</th>
                <th scope="col">Lots</th>
                <th scope="col">Open price</th>
                <th scope="col">Stop loss</th>
                <th scope="col">Take profit</th>
              </tr>
            </thead>
            <tbody>
              {account.positions.map((p) => {
                const digits = live.quotes[p.symbol]?.digits ?? 5;
                return (
                  <tr key={p.position_id}>
                    <th scope="row">{p.symbol}</th>
                    <td>{p.side === "buy" ? "Buy" : "Sell"}</td>
                    <td>{formatLots(p.volume)}</td>
                    <td>{formatPrice(p.open_price, digits)}</td>
                    <td>{p.stop_loss === null ? "—" : formatPrice(p.stop_loss, digits)}</td>
                    <td>{p.take_profit === null ? "—" : formatPrice(p.take_profit, digits)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {account.pending_orders.length > 0 && (
        <>
          <h2>Pending orders</h2>
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Pending orders</caption>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Type</th>
                  <th scope="col">Side</th>
                  <th scope="col">Lots</th>
                  <th scope="col">Level</th>
                </tr>
              </thead>
              <tbody>
                {account.pending_orders.map(({ request: o }) => {
                  const digits = live.quotes[o.symbol]?.digits ?? 5;
                  return (
                    <tr key={o.order_id}>
                      <th scope="row">{o.symbol}</th>
                      <td>{o.order_type}</td>
                      <td>{o.side === "buy" ? "Buy" : "Sell"}</td>
                      <td>{formatLots(o.volume)}</td>
                      <td>{o.level === null ? "—" : formatPrice(o.level, digits)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h2>Prices</h2>
      <Quotes quotes={live.quotes} attribution={market?.attribution ?? null} />
    </div>
  );
}
