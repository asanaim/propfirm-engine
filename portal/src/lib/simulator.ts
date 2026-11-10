/**
 * The equity simulator and the trading-day reset clock (public tools).
 * The simulator draws possible equity curves for a strategy from its win
 * rate and reward-to-risk ratio; it uses a seeded generator, so the same
 * inputs always draw the same curves (repeatable, and testable).
 */

export interface SimInput {
  capital: number;
  winRate: number; // percent, 0 to 100
  rrr: number; // reward to risk, e.g. 1.5
  trades: number; // per curve
  lines: number; // curves
  risk: { kind: "percent"; value: number } | { kind: "amount"; value: number };
  seed?: number;
}

export interface SimResult {
  curves: number[][]; // equity after each trade, starting with the capital
  finals: number[];
  medianFinal: number;
  worstDrawdownPercent: number; // the deepest fall from a peak, over all curves
  profitableShare: number; // curves ending above the capital, 0 to 1
}

/** mulberry32: a small, fast, seeded generator of numbers in [0, 1). */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const SIM_LIMITS = { trades: 1000, lines: 50 };

export function simulate(input: SimInput): SimResult {
  const next = seeded(input.seed ?? 20261110);
  const trades = Math.min(Math.max(Math.floor(input.trades), 1), SIM_LIMITS.trades);
  const lines = Math.min(Math.max(Math.floor(input.lines), 1), SIM_LIMITS.lines);
  const p = Math.min(Math.max(input.winRate, 0), 100) / 100;
  const curves: number[][] = [];
  let worst = 0;
  for (let l = 0; l < lines; l++) {
    let equity = input.capital;
    let peak = equity;
    const curve = [equity];
    for (let t = 0; t < trades; t++) {
      const stake = input.risk.kind === "percent" ? (equity * input.risk.value) / 100 : input.risk.value;
      equity += next() < p ? stake * input.rrr : -stake;
      if (equity < 0) equity = 0;
      peak = Math.max(peak, equity);
      if (peak > 0) worst = Math.max(worst, ((peak - equity) / peak) * 100);
      curve.push(equity);
    }
    curves.push(curve);
  }
  const finals = curves.map((c) => c[c.length - 1] ?? input.capital);
  const sorted = [...finals].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const medianFinal = sorted.length % 2 ? (sorted[mid] ?? 0) : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2;
  return { curves, finals, medianFinal, worstDrawdownPercent: worst, profitableShare: finals.filter((f) => f > input.capital).length / finals.length };
}

/** The hour and minute an instant shows in a time zone. */
function wallClock(ms: number, zone: string): { y: number; m: number; d: number; h: number; min: number } {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(ms);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  return { y: get("year"), m: get("month"), d: get("day"), h: get("hour"), min: get("minute") };
}

/** The next 17:00 New York time after `now` (ms UTC): the trading day's
reset. New York is a whole number of hours from UTC, so the reset falls on
a UTC hour: step whole hours and let the browser's zone data handle
daylight saving. */
export function nextReset(now: number): number {
  const start = Math.floor(now / 3_600_000) * 3_600_000 + 3_600_000;
  for (let t = start; t < start + 26 * 3_600_000; t += 3_600_000) {
    const c = wallClock(t, "America/New_York");
    if (c.h === 17 && c.min === 0) return t;
  }
  throw new Error("no reset within 26 hours");
}

export function countdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h.toString()}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}
