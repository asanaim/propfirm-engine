import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { type Analysis, AnalysisPage } from "./AnalysisPage";

const ME = { cif: "CIF00000001", full_name: "Ada Trader", email: "a@example.com", accounts: [] };
const DATA: Analysis = {
  accounts: ["ACC00000001"],
  summary: { trades: 4, wins: 2, losses: 2, win_rate_percent: "50.00", net_cents: 3_000_000, avg_profit_cents: 3_000_000, avg_loss_cents: -1_500_000, reward_to_risk: "2.00", profit_factor: "2.00", expectancy_cents: 750_000, sharpe_per_trade: "0.31" },
  cumulative: [{ ts: 1, cents: 4_000_000 }, { ts: 2, cents: 2_000_000 }, { ts: 3, cents: 4_000_000 }, { ts: 4, cents: 3_000_000 }],
  symbols: [{ symbol: "EUR/USD", trades: 3, net_cents: 5_000_000 }, { symbol: "XAU/USD", trades: 1, net_cents: -2_000_000 }],
  months: [{ month: "2026-10", trades: 3, net_cents: 4_000_000, win_rate_percent: "66.67", reward_to_risk: "1.50", expectancy_cents: 1_333_333, avg_hold_minutes: 1_460 }],
};

it("shows the trader's figures and charts", async () => {
  renderWith(<AnalysisPage />, fakeAuth(), fakeFetch([["GET", "/me", 200, ME], ["GET", "/me/analysis?", 200, DATA]]));
  expect(await screen.findByText("50.00 %")).toBeInTheDocument();
  expect(screen.getByText("$7,500.00")).toBeInTheDocument(); // expectancy
  expect(screen.getByRole("img", { name: /Cumulative profit, \$30,000\.00 after 4 trades/ })).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Result by symbol" })).toHaveTextContent("XAU/USD");
  expect(screen.getByText(/held 24\.3 h/)).toBeInTheDocument();
});

it("says so when there are no trades yet", async () => {
  const none = { ...DATA, summary: { ...DATA.summary, trades: 0 }, cumulative: [], symbols: [], months: [] };
  renderWith(<AnalysisPage />, fakeAuth(), fakeFetch([["GET", "/me", 200, ME], ["GET", "/me/analysis?", 200, none]]));
  expect(await screen.findByText(/No closed trades in this range yet/)).toBeInTheDocument();
});
