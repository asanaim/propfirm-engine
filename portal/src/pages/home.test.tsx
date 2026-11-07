import { describe, expect, it } from "vitest";
import type { LiveAccount, Me } from "../api/types";
import { balancePoints } from "../components/Gauges";
import { objectives } from "./AccountPage";
import { byDay, countdown } from "./NewsPage";
import { nextStep } from "./OverviewPage";

const ME: Me = {
  cif: "CIF00000001",
  email: "a@example.com",
  full_name: "Ada Trader",
  date_of_birth: "1990-01-01",
  residence_country: "DE",
  status: "registered",
  created_at: 0,
  accounts: [],
  orders: [],
  progressions: [],
  onboardings: [],
};

function live(change: Omit<Partial<LiveAccount>, "rules"> & { rules?: Record<string, unknown> }): LiveAccount {
  const { rules, ...rest } = change;
  return {
    account_id: "ACC1",
    status: "active",
    balance_cents: 510_000,
    equity_cents: 505_000,
    initial_balance_cents: 500_000,
    target_balance_cents: 530_000,
    daily_floor_cents: 485_000,
    max_floor_cents: 470_000,
    max_loss_limit_cents: 30_000,
    trading_days: 2,
    consistency_ok: true,
    ...rest,
    rules: { stage: "s1", min_trading_days: 4, consistency_percent: null, ...rules },
  } as unknown as LiveAccount;
}

describe("nextStep", () => {
  it("puts the most urgent thing first", () => {
    expect(nextStep(ME, {}).action).toBe("Choose a challenge");
    const order = { ord: "O1", product_id: "lite", size_cents: 500_000, status: "pending" } as Me["orders"][number];
    expect(nextStep({ ...ME, orders: [order] }, {}).action).toBe("Complete payment");
    const acc = { acc: "ACC1" } as Me["accounts"][number];
    const step = nextStep({ ...ME, accounts: [acc] }, { ACC1: live({}) });
    expect(step).toEqual({ text: "ACC1: $200.00 to the profit target.", href: "/accounts/ACC1", action: "Open the account" });
    expect(nextStep({ ...ME, accounts: [acc] }, { ACC1: live({ status: "passed" }) }).action).toBe("See accounts");
  });
});

describe("objectives", () => {
  it("lists what is left to pass", () => {
    const items = objectives(live({}));
    expect(items.map((i) => [i.label, i.state])).toEqual([
      ["Profit target", "open"],
      ["Minimum trading days", "open"],
      ["Daily loss limit", "done"],
      ["Maximum loss", "done"],
    ]);
    expect(items[0]?.detail).toBe("$200.00 to go");
    const breached = objectives(live({ status: "breached" }));
    expect(breached.find((i) => i.label === "Maximum loss")?.state).toBe("failed");
    const consistent = objectives(live({ consistency_ok: false, rules: { consistency_percent: 50 } }));
    expect(consistent.at(-1)?.state).toBe("open");
  });
});

describe("balancePoints", () => {
  it("follows closed trades, commissions and payouts", () => {
    const pts = balancePoints(
      [
        { type: "AccountOpened", ts: 10 },
        { type: "PositionOpened", ts: 20 },
        { type: "CommissionCharged", ts: 20, commission_cents: 350 },
        { type: "PositionClosed", ts: 30, pnl_cents: 12_000 },
        { type: "PayoutDebited", ts: 40, balance_cents: 500_000 },
      ],
      500_000,
    );
    expect(pts).toEqual([
      { ts: 10, cents: 500_000 },
      { ts: 20, cents: 499_650 },
      { ts: 30, cents: 511_650 },
      { ts: 40, cents: 500_000 },
    ]);
  });
});

describe("news helpers", () => {
  it("counts down and groups by day in a zone", () => {
    expect(countdown(0)).toBe("now");
    expect(countdown(65_000)).toBe("1 m 05 s");
    expect(countdown(3 * 3_600_000 + 5 * 60_000)).toBe("3 h 05 m");
    expect(countdown(2 * 86_400_000 + 4 * 3_600_000)).toBe("2 d 4 h");
    const at = Date.UTC(2026, 10, 10, 23, 30);
    const days = byDay([{ event_id: "a", start_ts: at, end_ts: at + 1, symbols: [], title: null }], "Asia/Dhaka");
    expect(days[0]?.day).toBe("2026-11-11"); // already the next day in Dhaka
    expect(byDay([{ event_id: "a", start_ts: at, end_ts: at + 1, symbols: [], title: null }], "UTC")[0]?.day).toBe("2026-11-10");
  });
});
