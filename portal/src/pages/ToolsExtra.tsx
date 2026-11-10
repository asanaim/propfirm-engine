import { useEffect, useMemo, useState } from "react";
import { useApi } from "../api/ApiProvider";
import { Loading, Notice } from "../components/ui";
import { parse, type ToolInstrument, usd } from "../lib/calculators";
import { countdown, nextReset, SIM_LIMITS, simulate } from "../lib/simulator";
import { Link, usePath } from "../router";

const TOOL_PAGES = [
  { href: "/tools", label: "Calculators" },
  { href: "/tools/symbols", label: "Symbols" },
  { href: "/tools/reset", label: "Daily reset" },
  { href: "/tools/simulator", label: "Equity simulator" },
];

/** The tools' own menu, on each tool page. */
export function ToolsNav() {
  const path = usePath();
  return (
    <nav className="tool-tabs" aria-label="Tools">
      {TOOL_PAGES.map((t) => (
        <Link key={t.href} href={t.href} className={path === t.href ? "active" : undefined} aria-current={path === t.href ? "page" : undefined}>
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

const CLASSES: Record<string, string> = { forex: "Forex", metal: "Metals", oil: "Energy", crypto: "Crypto" };

/** Symbol specifications (public, as FTMO and FundedNext show): what one
lot is, the pip, leverage and commission. Live prices are on each account
page once signed in (the price licence is for signed-in users). */
export function SymbolsPage() {
  const api = useApi();
  const [list, setList] = useState<ToolInstrument[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState("forex");
  const [query, setQuery] = useState("");
  useEffect(() => {
    api.get<ToolInstrument[]>("/tools/instruments").then(setList, () => {
      setFailed(true);
    });
  }, [api]);
  const classes = useMemo(() => [...new Set((list ?? []).map((i) => i.asset_class))], [list]);
  const q = query.trim().toUpperCase().replace("/", "");
  const shown = (list ?? []).filter((i) => (q === "" ? i.asset_class === tab : i.symbol.replace("/", "").includes(q)));
  return (
    <div className="page tools">
      <h1>Symbols</h1>
      <p className="lead">Every symbol you can trade, with what one lot is, the size of a pip and the leverage. Live prices and spreads show on your account page once you sign in.</p>
      <ToolsNav />
      {failed ? <Notice tone="error">The symbols could not load. Try again later.</Notice> : null}
      {list === null && !failed ? <Loading /> : null}
      {list !== null ? (
        <section className="card">
          <div className="symbol-bar">
            <div className="tool-tabs" role="tablist" aria-label="Asset classes">
              {classes.map((c) => (
                <button key={c} type="button" role="tab" aria-selected={q === "" && tab === c} className={q === "" && tab === c ? "active" : undefined} onClick={() => { setTab(c); setQuery(""); }}>
                  {CLASSES[c] ?? c}
                </button>
              ))}
            </div>
            <input className="symbol-search" type="search" placeholder="Search a symbol (EUR/USD)" aria-label="Search a symbol" value={query} onChange={(e) => { setQuery(e.target.value); }} />
          </div>
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Symbol specifications</caption>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">One lot</th>
                  <th scope="col">Pip or point</th>
                  <th scope="col">Leverage</th>
                  <th scope="col">Commission</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={i.symbol}>
                    <th scope="row">{i.symbol}</th>
                    <td>{i.contract_size.toLocaleString("en-US")} {i.asset_class === "forex" ? "units" : i.asset_class === "crypto" ? "coin" : "units"}</td>
                    <td>{i.pip}</td>
                    <td>1:{(Number(i.leverage.split("/")[0]) / Number(i.leverage.split("/")[1] ?? 1)).toLocaleString("en-US", { maximumFractionDigits: 2 })}</td>
                    <td>{i.commission_percent === "0" ? "None" : `${i.commission_percent} % of the trade`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {shown.length === 0 ? <p className="muted">No symbol matches.</p> : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function zoneName(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "your time zone";
  }
}

/** When the trading day resets (17:00 New York) in the trader's own time,
with a live countdown: daily loss limits are counted from it. */
export function ResetPage({ now: fixed }: { now?: number }) {
  const [now, setNow] = useState(() => fixed ?? Date.now());
  useEffect(() => {
    if (fixed !== undefined) return;
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(id);
    };
  }, [fixed]);
  const reset = nextReset(now);
  const local = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23" });
  const ny = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York", hourCycle: "h23" });
  return (
    <div className="page tools">
      <h1>Daily reset</h1>
      <p className="lead">The trading day runs from 17:00 to 17:00 New York time. Your daily loss limit starts again at each reset.</p>
      <ToolsNav />
      <section className="card">
        <div className="tool-grid">
          <div>
            <span className="muted">Time now in New York</span>
            <p className="tool-big">{ny.format(now)}</p>
          </div>
          <div>
            <span className="muted">Next reset in {zoneName()}</span>
            <p className="tool-big">{local.format(reset)}</p>
          </div>
          <div>
            <span className="muted">Time left today</span>
            <p className="tool-big" aria-live="off">{countdown(reset - now)}</p>
          </div>
        </div>
        <p className="muted small">New York moves its clocks for daylight saving; the reset follows New York, so in your time it may shift by an hour twice a year.</p>
      </section>
    </div>
  );
}

const W = 720;
const H = 260;

/** Possible equity curves for a strategy (public, like FTMO's simulator):
the same inputs always draw the same curves. */
export function SimulatorPage() {
  const [v, setV] = useState({ capital: "100000", win: "50", rrr: "1.5", trades: "100", lines: "10", risk: "1" });
  const [kind, setKind] = useState<"percent" | "amount">("percent");
  const [seed, setSeed] = useState(20261110);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setV({ ...v, [k]: e.target.value });
  };
  const n = { capital: parse(v.capital), win: parse(v.win), rrr: parse(v.rrr), trades: parse(v.trades), lines: parse(v.lines), risk: parse(v.risk) };
  const ok = Object.values(n).every((x) => x !== null && x >= 0) && (n.capital ?? 0) > 0;
  const result = ok
    ? simulate({ capital: n.capital ?? 0, winRate: n.win ?? 0, rrr: n.rrr ?? 0, trades: n.trades ?? 1, lines: n.lines ?? 1, risk: { kind, value: n.risk ?? 0 }, seed })
    : null;
  const all = result === null ? [0, 1] : result.curves.flat();
  const top = Math.max(...all);
  const bottom = Math.min(...all);
  const pad = (top - bottom) * 0.08 || 1;
  const hi = top + pad;
  const lo = Math.max(0, bottom - pad);
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const steps = result?.curves[0]?.length ?? 1;
  const path = (c: number[]) => c.map((v, i) => `${i === 0 ? "M" : "L"}${((i / Math.max(steps - 1, 1)) * W).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const field = (k: keyof typeof v, label: string) => (
    <label className="field">
      <span>{label}</span>
      <input inputMode="decimal" value={v[k]} onChange={set(k)} />
    </label>
  );
  return (
    <div className="page tools">
      <h1>Equity simulator</h1>
      <p className="lead">See the range of outcomes a strategy can have: set your capital, win rate and reward to risk, and the simulator draws possible equity curves.</p>
      <ToolsNav />
      <section className="card">
        <div className="tool-grid">
          {field("capital", "Capital (USD)")}
          {field("win", "Win rate (%)")}
          {field("rrr", "Reward to risk")}
          {field("trades", `Trades per curve (up to ${SIM_LIMITS.trades.toString()})`)}
          {field("lines", `Curves (up to ${SIM_LIMITS.lines.toString()})`)}
          <label className="field">
            <span>Risk per trade</span>
            <select value={kind} onChange={(e) => { setKind(e.target.value === "amount" ? "amount" : "percent"); }}>
              <option value="percent">Percent of equity</option>
              <option value="amount">Fixed amount (USD)</option>
            </select>
          </label>
          {field("risk", kind === "percent" ? "Risk (%)" : "Risk (USD)")}
        </div>
        <div className="row" style={{ marginTop: "var(--space-4)" }}>
          <button type="button" className="button button-secondary" onClick={() => { setSeed((s) => s + 1); }}>
            Run again
          </button>
        </div>
        {result === null ? (
          <p className="muted">Fill in every field with a number.</p>
        ) : (
          <>
            <svg className="sim-chart" viewBox={`0 0 ${W.toString()} ${H.toString()}`} role="img" aria-label={`${result.curves.length.toString()} simulated equity curves`} preserveAspectRatio="none">
              <line x1="0" x2={W} y1={y(n.capital ?? 0)} y2={y(n.capital ?? 0)} className="sim-start" />
              {result.curves.map((c, i) => (
                <path key={i} d={path(c)} className={(c[c.length - 1] ?? 0) >= (n.capital ?? 0) ? "sim-up" : "sim-down"} />
              ))}
            </svg>
            <div className="tool-grid sim-stats">
              <div className="tool-answer">
                <span className="muted">Middle outcome</span>
                <strong>{usd(result.medianFinal)}</strong>
              </div>
              <div className="tool-answer">
                <span className="muted">Curves ending in profit</span>
                <strong>{Math.round(result.profitableShare * 100).toString()} %</strong>
              </div>
              <div className="tool-answer">
                <span className="muted">Deepest drawdown</span>
                <strong>{result.worstDrawdownPercent.toFixed(1)} %</strong>
              </div>
            </div>
          </>
        )}
        <p className="muted small">A simulation, not a forecast: real trades are not independent coin flips, and past results do not promise future ones.</p>
      </section>
    </div>
  );
}
