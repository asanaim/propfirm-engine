import { AlertTriangle, CheckCircle2, Circle, Radio, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { Attribution as AttributionSource, Bar, LiveAccount, Market, Quote } from "../api/types";
import { useAccountStream } from "../api/useAccountStream";
import { useAuth } from "../auth/AuthProvider";
import { Attribution } from "../components/Attribution";
import { BalanceCurve, balancePoints, type CurvePoint, type Level, Ring } from "../components/Gauges";
import { OrderTicket } from "../components/OrderTicket";
import { PendingRow, PositionRow, TrailingRow } from "../components/PositionRow";
import { PriceChart } from "../components/PriceChart";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice, ScrollRegion } from "../components/ui";
import { formatCents, formatDuration, formatPrice } from "../lib/money";
import { Link } from "../router";
import { loadHistory } from "./HistoryPage";

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

/** Loss room left before a floor: tone by how much of the limit is used. */
function LossCard({ title, equity, floor, limit }: { title: string; equity: number; floor: number; limit: number }) {
  const left = equity - floor;
  const share = limit > 0 ? clamp(left / limit) : 0;
  const tone = share > 0.5 ? "ok" : share > 0.2 ? "warn" : "danger";
  return (
    <Card title={title}>
      <div className="gauge-row">
        <Ring value={share} label={`${title}: share of the limit left`} tone={tone}>
          {Math.round(share * 100)}%
        </Ring>
        <div>
          <p className="figure">{formatCents(Math.max(0, left))}</p>
          <p className="muted">left of {formatCents(limit)}</p>
        </div>
      </div>
      <dl className="facts">
        <dt>Floor (equity)</dt>
        <dd>{formatCents(floor)}</dd>
      </dl>
    </Card>
  );
}

export function AccountTabs({ acc, current }: { acc: string; current: "dashboard" | "history" | "rules" }) {
  const tabs = [
    { key: "dashboard", href: `/accounts/${acc}`, label: "Dashboard" },
    { key: "history", href: `/accounts/${acc}/history`, label: "History" },
    { key: "rules", href: `/accounts/${acc}/rules`, label: "Rules" },
  ];
  return (
    <nav className="tabs" aria-label="Account">
      {tabs.map((t) => (
        <Link key={t.key} href={t.href} aria-current={t.key === current ? "page" : undefined}>
          {t.label}
        </Link>
      ))}
    </nav>
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
      <div className="gauge-row">
        <Ring value={share} label="Profit target reached" tone="ok">
          {Math.round(share * 100)}%
        </Ring>
        <div>
          <p className="figure">{formatCents(Math.max(0, target - account.balance_cents))}</p>
          <p className="muted">to go, on the closed balance (target {formatCents(target)})</p>
        </div>
      </div>
    </Card>
  );
}

function Quotes({ quotes, attribution }: { quotes: Record<string, Quote>; attribution: AttributionSource | null }) {
  const list = Object.values(quotes).sort((a, b) => a.symbol.localeCompare(b.symbol));
  return (
    <>
      <ScrollRegion label="Prices">
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
                <td>{q.open === false ? "Market closed" : q.frozen ? "No price" : q.blocked ? "News window" : "Open"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollRegion>
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

type Check = "done" | "open" | "failed";

/** What is left to pass this stage, item by item. */
export function objectives(account: LiveAccount): { label: string; detail: string; state: Check }[] {
  const failed = account.status === "breached" || account.status === "voided";
  const out: { label: string; detail: string; state: Check }[] = [];
  const target = account.target_balance_cents;
  if (target !== null) {
    const left = target - account.balance_cents;
    out.push({
      label: "Profit target",
      detail: left <= 0 ? `reached (${formatCents(target)})` : `${formatCents(left)} to go`,
      state: left <= 0 ? "done" : "open",
    });
  }
  const days = account.rules.min_trading_days;
  if (days > 0) {
    out.push({
      label: "Minimum trading days",
      detail: `${account.trading_days} of ${days}`,
      state: account.trading_days >= days ? "done" : "open",
    });
  }
  out.push({
    label: "Daily loss limit",
    detail: failed ? "breached" : `kept · ${formatCents(Math.max(0, account.equity_cents - account.daily_floor_cents))} room today`,
    state: failed ? "failed" : "done",
  });
  out.push({
    label: "Maximum loss",
    detail: failed ? "breached" : `kept · ${formatCents(Math.max(0, account.equity_cents - account.max_floor_cents))} room`,
    state: failed ? "failed" : "done",
  });
  if (account.rules.consistency_percent !== null) {
    out.push({
      label: `Consistency (best day at most ${account.rules.consistency_percent} %)`,
      detail: account.consistency_ok ? "met" : "not met yet",
      state: account.consistency_ok ? "done" : "open",
    });
  }
  return out;
}

function Objectives({ account }: { account: LiveAccount }) {
  const items = objectives(account);
  const done = items.filter((i) => i.state === "done").length;
  const icon = { done: CheckCircle2, open: Circle, failed: XCircle };
  return (
    <Card title={account.target_balance_cents === null ? "Account rules" : "To pass this stage"}>
      <p className="muted">
        {done} of {items.length} met{account.status === "passed" ? " · passed" : ""}
      </p>
      <ul className="objectives">
        {items.map((i) => {
          const Icon = icon[i.state];
          return (
            <li key={i.label} className={`objective objective-${i.state}`}>
              <Icon aria-hidden="true" />
              <span>
                <strong>{i.label}</strong>
                <span className="muted">{i.detail}</span>
              </span>
              <span className="visually-hidden">{i.state === "done" ? "met" : i.state === "failed" ? "failed" : "open"}</span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Curve({ acc, account, now }: { acc: string; account: LiveAccount; now: number }) {
  const api = useApi();
  const [points, setPoints] = useState<CurvePoint[] | null>(null);
  const balance = account.balance_cents;
  useEffect(() => {
    let live = true;
    loadHistory(api.get, acc).then(
      (events) => {
        if (live) setPoints(balancePoints(events, account.initial_balance_cents));
      },
      () => {
        if (live) setPoints([]);
      },
    );
    return () => {
      live = false;
    };
    // the history only changes when the balance does
  }, [api, acc, balance, account.initial_balance_cents]);
  const levels: Level[] = [{ cents: account.max_floor_cents, label: "Maximum loss floor", kind: "floor" }];
  if (account.target_balance_cents !== null) {
    levels.unshift({ cents: account.target_balance_cents, label: "Target", kind: "target" });
  }
  const start = points?.[0] ?? { ts: now - 60_000, cents: account.initial_balance_cents };
  return (
    <Card title="Balance">
      <p className="figure">{formatCents(account.balance_cents)}</p>
      <p className="muted">
        started at {formatCents(account.initial_balance_cents)} · equity {formatCents(account.equity_cents)}
      </p>
      {points === null ? (
        <Loading label="Loading the curve" />
      ) : (
        <BalanceCurve
          points={points.length > 0 ? points : [start]}
          levels={levels}
          now={{ ts: Math.max(now, (points.at(-1) ?? start).ts + 1), cents: account.equity_cents }}
        />
      )}
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
  const canTrade = account.status === "active" && !account.locked;
  const digitsOf = (symbol: string) =>
    market?.symbols.find((m) => m.symbol === symbol)?.digits ?? live.quotes[symbol]?.digits ?? 5;
  return (
    <div className="page">
      <p className="crumbs">
        <Link href="/accounts">Accounts</Link> / {acc}
      </p>
      <AccountTabs acc={acc} current="dashboard" />
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
      <div className="dash-hero">
        <Curve acc={acc} account={account} now={now} />
        <Objectives account={account} />
      </div>
      <div className="grid">
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
      {market !== null && (
        <OrderTicket acc={acc} market={market} quotes={live.quotes} disabled={!canTrade} />
      )}
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
                <th scope="col">Current</th>
                <th scope="col">Profit</th>
                <th scope="col">Stop loss / take profit</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {account.positions.map((p) => (
                <PositionRow
                  key={p.position_id}
                  acc={acc}
                  position={p}
                  digits={digitsOf(p.symbol)}
                  canTrade={canTrade}
                  trailing={account.trailing_orders.find((t) => t.kind === "stop" && t.position_id === p.position_id)}
                />
              ))}
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
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {account.pending_orders.map((o) => (
                  <PendingRow key={o.request.order_id} acc={acc} order={o} digits={digitsOf(o.request.symbol)} canTrade={canTrade} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {account.trailing_orders.length > 0 && (
        <>
          <h2>Trailing orders</h2>
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Trailing orders</caption>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Type</th>
                  <th scope="col">Lots</th>
                  <th scope="col">Distance / step (points)</th>
                  <th scope="col">Level</th>
                  <th scope="col">Level as of</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {account.trailing_orders.map((o) => (
                  <TrailingRow key={o.order_id} acc={acc} order={o} digits={digitsOf(o.symbol)} canTrade={canTrade} />
                ))}
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
