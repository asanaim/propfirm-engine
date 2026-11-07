/** Dashboard visuals: a ring gauge and the balance curve. Plain SVG: no
chart library for two shapes, and every number stays in integer cents until
it is drawn. */

import { useId } from "react";
import { formatCents } from "../lib/money";

export type Tone = "ok" | "warn" | "danger" | "neutral";

/** A ring that fills to `value` (0–1), with `children` in the middle; a
meter to assistive technology. */
export function Ring({
  value,
  label,
  tone,
  size = 92,
  children,
}: {
  value: number;
  label: string;
  tone: Tone;
  size?: number;
  children?: React.ReactNode;
}) {
  const v = Math.min(1, Math.max(0, value));
  const r = 42;
  const length = 2 * Math.PI * r;
  return (
    <div
      className={`ring ring-${tone}`}
      style={{ width: size, height: size }}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
    >
      <svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">
        <circle className="ring-track" cx="50" cy="50" r={r} />
        <circle
          className="ring-value"
          cx="50"
          cy="50"
          r={r}
          strokeDasharray={`${(v * length).toFixed(2)} ${length.toFixed(2)}`}
          transform="rotate(-90 50 50)"
        />
      </svg>
      <span className="ring-label">{children}</span>
    </div>
  );
}

export interface CurvePoint {
  ts: number; // ms UTC
  cents: number;
}

export interface Level {
  cents: number;
  label: string;
  kind: "target" | "floor";
}

/** The balance after each closed trade, from the starting balance, plus
equity now; the profit target and the loss floors as lines. */
export function BalanceCurve({ points, levels, now }: { points: CurvePoint[]; levels: Level[]; now: CurvePoint }) {
  const id = useId().replace(/:/g, "");
  const all = [...points, now];
  const W = 640;
  const H = 240;
  const pad = { l: 8, r: 8, t: 18, b: 18 };
  const values = [...all.map((p) => p.cents), ...levels.map((l) => l.cents)];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const t0 = all[0]?.ts ?? 0;
  const t1 = Math.max(now.ts, t0 + 1);
  const x = (ts: number) => pad.l + ((ts - t0) / (t1 - t0)) * (W - pad.l - pad.r);
  const y = (c: number) => pad.t + (1 - (c - lo) / span) * (H - pad.t - pad.b);
  // a step line: the balance changes only when a trade closes
  let d = "";
  all.forEach((p, i) => {
    d += i === 0 ? `M${x(p.ts).toFixed(1)},${y(p.cents).toFixed(1)}` : ` H${x(p.ts).toFixed(1)} V${y(p.cents).toFixed(1)}`;
  });
  const last = all.at(-1) ?? now;
  const area = `${d} V${H - pad.b} H${x(t0).toFixed(1)} Z`;
  return (
    <figure className="curve" aria-label={`Balance ${formatCents(points[0]?.cents ?? now.cents)} at the start, ${formatCents(now.cents)} equity now`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--summit)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--summit)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {levels.map((l) => (
          <line key={l.label} className={`curve-level curve-${l.kind}`} x1={pad.l} x2={W - pad.r} y1={y(l.cents)} y2={y(l.cents)} />
        ))}
        <path d={area} fill={`url(#fill-${id})`} />
        <path className="curve-line" d={d} />
        <circle className="curve-dot" cx={x(last.ts)} cy={y(last.cents)} r="5" />
      </svg>
      {levels.map((l) => (
        <span key={l.label} className={`curve-tag curve-tag-${l.kind}`} style={{ top: `${((y(l.cents) / H) * 100).toFixed(2)}%` }}>
          {l.label} {formatCents(l.cents)}
        </span>
      ))}
    </figure>
  );
}

interface BalanceEvent {
  type: string;
  ts: number;
  [field: string]: unknown;
}

/** The balance over time from the account's journal history: the opening
balance, then each closed trade's profit, each commission and each payout
(which states the balance after it). */
export function balancePoints(events: BalanceEvent[], initial: number): CurvePoint[] {
  const opened = events.find((e) => e.type === "AccountOpened");
  let cents = initial;
  const out: CurvePoint[] = [{ ts: opened?.ts ?? events[0]?.ts ?? 0, cents }];
  for (const e of events) {
    if (e.type === "PositionClosed") cents += Number(e.pnl_cents);
    else if (e.type === "CommissionCharged") cents -= Number(e.commission_cents);
    else if (e.type === "PayoutDebited") cents = Number(e.balance_cents);
    else continue;
    out.push({ ts: e.ts, cents });
  }
  return out;
}
