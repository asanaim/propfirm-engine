import { screen, within, waitFor } from "@testing-library/react";
import type { LiveAccount } from "../api/types";
import { fakeAuth, renderWith } from "../test-utils";
import { AccountPage, ResetCard } from "./AccountPage";
import { RulesPage } from "./RulesPage";

const chart = vi.hoisted(() => ({
  setData: vi.fn(),
  update: vi.fn(),
  applyOptions: vi.fn(),
}));
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: {},
  createChart: () => ({
    applyOptions: vi.fn(),
    addSeries: () => chart,
    timeScale: () => ({ fitContent: vi.fn() }),
    remove: vi.fn(),
  }),
}));

const ACCOUNT: LiveAccount = {
  account_id: "ACC00000001",
  rsv_id: "lite-s1-v2",
  status: "active",
  locked: false,
  balance_cents: 10_200_000,
  equity_cents: 9_850_000,
  used_margin_cents: 0,
  free_margin_cents: 9_850_000,
  initial_balance_cents: 10_000_000,
  daily_floor_cents: 9_700_000,
  max_floor_cents: 9_400_000,
  daily_loss_limit_cents: 300_000,
  max_loss_limit_cents: 600_000,
  next_reset_ts: 1_793_000_000_000 + 3 * 3_600_000,
  next_daily_floor_cents: 9_900_000,
  consistency_ok: true,
  target_balance_cents: 10_600_000,
  rules: {
    stage: "s1",
    rsv_id: "lite-s1-v2",
    profit_target_percent: "6",
    max_loss_percent: "6",
    max_loss_kind: "static",
    daily_loss_percent: "3",
    min_trading_days: 4,
    consistency_percent: null,
    consistency_at_pass: false,
    target_basis: "closed_balance",
    flat_to_pass: true,
    tie_rule: "breach_wins",
    news_restricted: false, // evaluations: not bound by news windows
  },
  trading_days: 1,
  positions: [
    {
      position_id: "p1",
      symbol: "EUR/USD",
      side: "buy",
      volume: 1_000,
      open_price: 110_005,
      opened_at: 0,
      stop_loss: null,
      take_profit: 111_000,
      current_price: 109_655,
      pnl_cents: -350_000,
    },
  ],
  pending_orders: [],
  trailing_orders: [],
  engine_ts: 0,
};

const QUOTES = {
  "EUR/USD": {
    symbol: "EUR/USD",
    bid: 109_655,
    ask: 109_665,
    mid: 109_660,
    source_ts: 1,
    digits: 5,
    frozen: false,
    blocked: false,
  },
};

function server(account: LiveAccount = ACCOUNT) {
  const stream = `event: account\ndata: ${JSON.stringify(account)}\n\nevent: quotes\ndata: ${JSON.stringify(QUOTES)}\n\n`;
  return vi.fn<typeof fetch>((input) => {
    const url = input as string;
    if (url.endsWith("/stream"))
      return Promise.resolve(new Response(stream, { status: 200 }));
    if (url === "/api/market") {
      const market = {
        source: "synthetic",
        attribution: {
          text: "Data provided by Twelve Data",
          link: "Twelve Data",
          url: "https://twelvedata.com/",
        },
        symbols: [{ symbol: "EUR/USD", digits: 5 }],
        intervals: ["1m", "1h"],
      };
      return Promise.resolve(
        new Response(JSON.stringify(market), { status: 200 }),
      );
    }
    if (url.startsWith("/api/bars?symbol=EUR%2FUSD&interval=1m")) {
      const bars = [
        {
          time: 60_000,
          open: 110_000,
          high: 110_050,
          low: 109_950,
          close: 110_010,
          updates: 4,
        },
      ];
      return Promise.resolve(
        new Response(JSON.stringify(bars), { status: 200 }),
      );
    }
    return Promise.reject(new Error(`no route ${url}`));
  });
}

it("shows the account's numbers from the live stream", async () => {
  renderWith(<AccountPage acc="ACC00000001" />, fakeAuth(), server());
  const balance = (
    await screen.findByRole("heading", { name: "Balance" })
  ).closest("section");
  expect(balance).toHaveTextContent("$102,000.00");
  const daily = screen
    .getByRole("heading", { name: "Daily loss" })
    .closest("section");
  expect(daily).toHaveTextContent("$1,500.00left of $3,000.00"); // equity 98,500 - floor 97,000
  expect(daily).toHaveTextContent("$97,000.00");
  expect(within(daily as HTMLElement).getByRole("meter")).toHaveAttribute(
    "aria-valuenow",
    "50",
  );
  const max = screen
    .getByRole("heading", { name: "Maximum loss" })
    .closest("section");
  expect(max).toHaveTextContent("$4,500.00left of $6,000.00");
  expect(
    screen.getByRole("heading", { name: "Equity" }).closest("section"),
  ).toHaveTextContent("-$3,500.00 open");
  expect(
    screen.getByRole("heading", { name: "Profit target" }).closest("section"),
  ).toHaveTextContent("$4,000.00");
  const positions = screen.getByRole("table", { name: "Open positions" });
  const row = within(positions)
    .getByRole("rowheader", { name: "EUR/USD" })
    .closest("tr");
  expect(row).toHaveTextContent("Buy10.001.100051.09655-$3,500.00— / 1.11000");
  // Twelve Data's guidelines: this text near the data, "Twelve Data" a followed link
  const links = await screen.findAllByRole("link", { name: "Twelve Data" });
  expect(links).toHaveLength(2); // the chart and the price table
  for (const link of links) {
    expect(link).toHaveAttribute("href", "https://twelvedata.com/");
    expect(link.getAttribute("rel") ?? "").not.toContain("nofollow");
    expect(link.closest("p")).toHaveTextContent(
      /^Data provided by Twelve Data/,
    );
  }
  expect(screen.getByRole("link", { name: "TradingView" })).toHaveAttribute(
    "href",
    "https://www.tradingview.com/",
  );
  expect(
    await screen.findByRole("figure", { name: "EUR/USD 1m candles" }),
  ).toBeInTheDocument();
  // the bars arrive after the chart is drawn: wait for them (flaked on CI)
  await waitFor(() => {
    expect(chart.setData).toHaveBeenCalledWith([
      { time: 60, open: 1.1, high: 1.1005, low: 1.0995, close: 1.1001 },
    ]);
  });
  const quotes = screen.getByRole("table", { name: "Prices" });
  const prices = within(quotes)
    .getByRole("rowheader", { name: "EUR/USD" })
    .closest("tr");
  expect(prices).toHaveTextContent("1.096551.09665Open");
});

it("warns before the reset when equity is at or below the next floor", () => {
  const now = ACCOUNT.next_reset_ts - 3 * 3_600_000 - 7 * 60_000;
  renderWith(<ResetCard account={ACCOUNT} now={now} />, fakeAuth(), vi.fn());
  expect(screen.getByText("3 h 07 min")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "If equity is still at or below $99,000.00 at the reset",
  );
});

it("does not warn when equity is above the next floor", () => {
  const safe = { ...ACCOUNT, equity_cents: 9_900_001 };
  renderWith(<ResetCard account={safe} now={0} />, fakeAuth(), vi.fn());
  expect(screen.queryByRole("alert")).toBeNull();
});

it("names a source without a link as plain text", async () => {
  const { Attribution } = await import("../components/Attribution");
  renderWith(
    <Attribution
      source={{ text: "Synthetic prices (development)", link: null, url: null }}
    />,
    fakeAuth(),
    vi.fn(),
  );
  expect(
    screen.getByText("Synthetic prices (development)"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});

it("says whether news windows bind the account's stage", async () => {
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server());
  expect(
    await screen.findByText(/News events do not restrict this stage/),
  ).toBeInTheDocument();
});

it("warns a funded account that a fill inside a news window can end it", async () => {
  const funded = {
    ...ACCOUNT,
    target_balance_cents: null,
    rules: {
      ...ACCOUNT.rules,
      stage: "funded" as const,
      news_restricted: true,
    },
  };
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server(funded));
  expect(
    await screen.findByText(/a violation can end the account/),
  ).toBeInTheDocument();
});

it("tells a funded trader that trades close before a long market break", async () => {
  const funded = {
    ...ACCOUNT,
    target_balance_cents: null,
    rules: {
      ...ACCOUNT.rules,
      stage: "funded" as const,
      flat_before_breaks: true,
    },
  };
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server(funded));
  expect(
    await screen.findByText(
      /closed and pending orders cancelled a few minutes ahead/,
    ),
  ).toBeInTheDocument();
});

it("says nothing of market breaks to an evaluation", async () => {
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server());
  await screen.findByText(/News events do not restrict this stage/);
  expect(screen.queryByText(/a few minutes ahead/)).toBeNull();
});

it("tells a funded trader on the news rule as FTMO that a fill in a window ends the account", async () => {
  const funded = {
    ...ACCOUNT,
    target_balance_cents: null,
    rules: {
      ...ACCOUNT.rules,
      stage: "funded" as const,
      news_restricted: true,
      news_breaches: true,
    },
  };
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server(funded));
  expect(
    await screen.findByText(
      /Pending orders on those symbols are cancelled when/,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/fills inside a window ends the account/),
  ).toBeInTheDocument();
});

it("tells an Instant trader whether a payout moves the trailing floor", async () => {
  const kept = {
    ...ACCOUNT,
    target_balance_cents: null,
    rules: {
      ...ACCOUNT.rules,
      stage: "funded" as const,
      max_loss_kind: "trailing" as const,
      floor_restarts_after_payout: false,
    },
  };
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server(kept));
  expect(
    await screen.findByText(/A payout does not move the floor down again/),
  ).toBeInTheDocument();
});

it("tells an older Instant account that its floor is set again after a payout", async () => {
  const restarts = {
    ...ACCOUNT,
    target_balance_cents: null,
    rules: {
      ...ACCOUNT.rules,
      stage: "funded" as const,
      max_loss_kind: "trailing" as const,
      floor_restarts_after_payout: true,
    },
  };
  renderWith(<RulesPage acc="ACC00000001" />, fakeAuth(), server(restarts));
  expect(
    await screen.findByText(/After a payout the floor is set again/),
  ).toBeInTheDocument();
});
