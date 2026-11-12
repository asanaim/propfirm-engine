import { useEffect, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatCents } from "../lib/money";

/** GET /me/analysis: figures from the trader's own closed trades. */
export interface Analysis {
  accounts: string[];
  summary: {
    trades: number;
    wins: number;
    losses: number;
    win_rate_percent: string | null;
    net_cents: number;
    avg_profit_cents: number;
    avg_loss_cents: number;
    reward_to_risk: string | null;
    profit_factor: string | null;
    expectancy_cents: number;
    sharpe_per_trade: string | null;
  };
  cumulative: { ts: number; cents: number }[];
  symbols: { symbol: string; trades: number; net_cents: number }[];
  months: { month: string; trades: number; net_cents: number; win_rate_percent: string | null; reward_to_risk: string | null; expectancy_cents: number; avg_hold_minutes: number }[];
}

const W = 720;
const H = 220;

function Tile({ label, value, tone, hint }: { label: string; value: string; tone?: "up" | "down" | undefined; hint?: string }) {
  return (
    <div className="stat-tile" title={hint}>
      <span className="muted">{label}</span>
      <strong className={tone === undefined ? undefined : `tone-${tone}`}>{value}</strong>
    </div>
  );
}

const money = (c: number) => formatCents(c);
const sign = (c: number): "up" | "down" | undefined => (c > 0 ? "up" : c < 0 ? "down" : undefined);

function CumulativeChart({ points }: { points: Analysis["cumulative"] }) {
  const values = [0, ...points.map((p) => p.cents)];
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  const span = hi - lo || 1;
  const y = (v: number) => H - ((v - lo) / span) * (H - 16) - 8;
  const x = (i: number) => (i / Math.max(points.length, 1)) * W;
  const d = ["M0," + y(0).toFixed(1), ...points.map((p, i) => `L${x(i + 1).toFixed(1)},${y(p.cents).toFixed(1)}`)].join("");
  return (
    <svg className="an-chart" viewBox={`0 0 ${W.toString()} ${H.toString()}`} preserveAspectRatio="none" role="img" aria-label={`Cumulative profit, ${money(points.at(-1)?.cents ?? 0)} after ${points.length.toString()} trades`}>
      <line x1="0" x2={W} y1={y(0)} y2={y(0)} className="an-zero" />
      <path d={`${d}L${W.toString()},${y(0).toFixed(1)}L0,${y(0).toFixed(1)}Z`} className="an-area" />
      <path d={d} className="an-line" />
    </svg>
  );
}

function Bars({ rows, label }: { rows: { key: string; cents: number; note: string }[]; label: string }) {
  const most = Math.max(1, ...rows.map((r) => Math.abs(r.cents)));
  return (
    <ul className="an-bars" aria-label={label}>
      {rows.map((r) => (
        <li key={r.key}>
          <span className="an-key">{r.key}</span>
          <span className="an-track">
            <span className={`an-bar ${r.cents < 0 ? "down" : "up"}`} style={{ width: `${((Math.abs(r.cents) / most) * 100).toFixed(1)}%` }} />
          </span>
          <span className={`an-value tone-${r.cents < 0 ? "down" : "up"}`}>{money(r.cents)}</span>
          <span className="muted small">{r.note}</span>
        </li>
      ))}
    </ul>
  );
}

const trades = (n: number) => `${n.toString()} trade${n === 1 ? "" : "s"}`;
const hold = (m: number) => (m >= 60 ? `${(m / 60).toFixed(1)} h` : `${m.toString()} min`);
const day = (s: string): number | null => (s === "" ? null : Date.parse(`${s}T00:00:00Z`));

/** Trader's analysis (as FTMO shows): the trader's own results over all
accounts or one, in a date range. */
export function AnalysisPage() {
  const auth = useAuth();
  const me = useMe();
  const api = useApi();
  const [account, setAccount] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [data, setData] = useState<Analysis | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const ready = me.status === "ready";
  useEffect(() => {
    if (!ready) return;
    const q = new URLSearchParams();
    if (account !== "") q.set("account", account);
    const since = day(from);
    const until = day(to);
    if (since !== null) q.set("since", since.toString());
    if (until !== null) q.set("until", (until + 86_400_000).toString()); // the whole last day
    let live = true;
    api.get<Analysis>(`/me/analysis?${q.toString()}`).then(
      (a) => {
        if (!live) return;
        setFailed(null);
        setData(a);
      },
      (e: unknown) => {
        if (live) setFailed(e);
      },
    );
    return () => {
      live = false;
    };
  }, [api, ready, account, from, to]);
  if (me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page">
        <h1>Trader&apos;s analysis</h1>
        <Button onClick={() => void auth.signIn("/analysis")}>Sign in to see your analysis</Button>
      </div>
    );
  }
  if (me.status === "unregistered") return <Notice tone="info">Create your trader profile first.</Notice>;
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const s = data?.summary;
  return (
    <div className="page analysis">
      <h1>Trader&apos;s analysis</h1>
      <p className="lead">Your results across your accounts, from your closed trades. Each part of a position closed counts as a trade; commissions are taken off.</p>
      <Card>
        <div className="tool-grid">
          <label className="field">
            <span>Account</span>
            <select value={account} onChange={(e) => { setAccount(e.target.value); }}>
              <option value="">All accounts</option>
              {(data?.accounts ?? []).map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>From</span>
            <input type="date" value={from} onChange={(e) => { setFrom(e.target.value); }} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={to} onChange={(e) => { setTo(e.target.value); }} />
          </label>
        </div>
      </Card>
      {failed !== null ? <Problem error={failed} /> : null}
      {data === null && failed === null ? <Loading /> : null}
      {s !== undefined && s.trades === 0 ? <Notice tone="info">No closed trades in this range yet. Your figures appear here after your first closed trade.</Notice> : null}
      {s !== undefined && s.trades > 0 && data !== null ? (
        <>
          <div className="stat-grid">
            <Tile label="Trades" value={s.trades.toLocaleString("en-US")} />
            <Tile label="Win rate" value={s.win_rate_percent === null ? "None" : `${s.win_rate_percent} %`} />
            <Tile label="Average profit" value={s.wins === 0 ? "None" : money(s.avg_profit_cents)} tone={s.wins === 0 ? undefined : "up"} />
            <Tile label="Average loss" value={s.losses === 0 ? "None" : money(s.avg_loss_cents)} tone={s.losses === 0 ? undefined : "down"} />
            <Tile label="Reward to risk" value={s.wins === 0 ? "None" : (s.reward_to_risk ?? "None")} hint="Average profit divided by average loss" />
            <Tile label="Profit factor" value={s.profit_factor ?? "None"} hint="Total won divided by total lost" />
            <Tile label="Expectancy" value={money(s.expectancy_cents)} tone={sign(s.expectancy_cents)} hint="Average result per trade" />
            <Tile label="Sharpe (per trade)" value={s.sharpe_per_trade ?? "None"} hint="Average result divided by its spread" />
          </div>
          <Card title="Cumulative profit">
            <CumulativeChart points={data.cumulative} />
            <p className="muted small">Net {money(s.net_cents)} over {s.trades.toString()} trades.</p>
          </Card>
          <div className="grid">
            <Card title="Result by month">
              <Bars label="Result by month" rows={data.months.map((m) => ({ key: m.month, cents: m.net_cents, note: `${trades(m.trades)}, win rate ${m.win_rate_percent ?? "none"} %, held ${hold(m.avg_hold_minutes)}` }))} />
            </Card>
            <Card title="Result by symbol">
              <Bars label="Result by symbol" rows={data.symbols.map((x) => ({ key: x.symbol, cents: x.net_cents, note: trades(x.trades) }))} />
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}
