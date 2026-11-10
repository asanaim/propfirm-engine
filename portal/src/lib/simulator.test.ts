import { describe, expect, it } from "vitest";
import { countdown, nextReset, seeded, simulate } from "./simulator";

describe("equity simulator", () => {
  const base = { capital: 100_000, winRate: 50, rrr: 1.5, trades: 100, lines: 10, risk: { kind: "percent" as const, value: 1 } };

  it("draws the same curves for the same inputs", () => {
    expect(simulate(base)).toEqual(simulate(base));
    expect(simulate({ ...base, seed: 2 }).finals).not.toEqual(simulate({ ...base, seed: 3 }).finals);
    const r = seeded(1);
    for (let i = 0; i < 1000; i++) {
      const x = r();
      expect(x >= 0 && x < 1).toBe(true);
    }
  });

  it("follows the edge: always winning grows, always losing shrinks", () => {
    const win = simulate({ ...base, winRate: 100, trades: 2, lines: 1, risk: { kind: "amount", value: 1_000 } });
    expect(win.curves[0]).toEqual([100_000, 101_500, 103_000]);
    expect(win.worstDrawdownPercent).toBe(0);
    const lose = simulate({ ...base, winRate: 0, trades: 2, lines: 1, risk: { kind: "percent", value: 10 } });
    expect(lose.curves[0]).toEqual([100_000, 90_000, 81_000]);
    expect(lose.worstDrawdownPercent).toBeCloseTo(19, 10);
    expect(lose.profitableShare).toBe(0);
  });

  it("never goes below zero and caps the work", () => {
    const r = simulate({ ...base, winRate: 0, trades: 5, lines: 1, risk: { kind: "amount", value: 60_000 } });
    expect(Math.min(...(r.curves[0] ?? []))).toBe(0);
    expect(simulate({ ...base, trades: 10_000, lines: 999 }).curves).toHaveLength(50);
  });
});

describe("daily reset", () => {
  it("is 17:00 New York, summer (UTC-4) and winter (UTC-5)", () => {
    expect(new Date(nextReset(Date.UTC(2026, 6, 1, 12, 0))).toISOString()).toBe("2026-07-01T21:00:00.000Z");
    expect(new Date(nextReset(Date.UTC(2026, 11, 1, 12, 0))).toISOString()).toBe("2026-12-01T22:00:00.000Z");
    expect(new Date(nextReset(Date.UTC(2026, 11, 1, 22, 0))).toISOString()).toBe("2026-12-02T22:00:00.000Z"); // at the reset: the next one
  });

  it("counts down in hours, minutes and seconds", () => {
    expect(countdown(5 * 3_600_000 + 43 * 60_000 + 7_000)).toBe("5:43:07");
    expect(countdown(-5)).toBe("0:00:00");
  });
});
