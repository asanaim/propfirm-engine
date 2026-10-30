import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LivePendingOrder, LivePosition, Market } from "../api/types";
import { describeOutcome, OrderTicket } from "../components/OrderTicket";
import { PendingRow, PositionRow } from "../components/PositionRow";
import { fakeAuth, renderWith } from "../test-utils";
import { tradesCsv, tradesOf, type HistoryEvent } from "./HistoryPage";
import { NewsPage } from "./NewsPage";

const MARKET: Market = {
  source: "synthetic",
  attribution: { text: "Synthetic prices (development)", link: null, url: null },
  symbols: [
    { symbol: "EUR/USD", digits: 5 },
    { symbol: "USD/JPY", digits: 3 },
  ],
  intervals: ["1m"],
};
const QUOTES = {
  "EUR/USD": { symbol: "EUR/USD", bid: 109_995, ask: 110_005, mid: 110_000, source_ts: 1, digits: 5, frozen: false, blocked: false },
};

type Call = { url: string; method: string; body: unknown };

function recorder(answer: (call: Call) => [number, unknown]) {
  const calls: Call[] = [];
  const fetcher = vi.fn<typeof fetch>((input, init) => {
    const call = {
      url: input as string,
      method: init?.method ?? "GET",
      body: init?.body === undefined ? undefined : (JSON.parse(init.body as string) as unknown),
    };
    calls.push(call);
    const [status, body] = answer(call);
    return Promise.resolve(new Response(JSON.stringify(body), { status }));
  });
  return { calls, fetcher };
}

it("sends a market order in integer lots and points, and says how it went", async () => {
  const fill = { status: "done", events: [{ type: "PositionOpened", price: 110_005 }] };
  const { calls, fetcher } = recorder(() => [200, fill]);
  renderWith(<OrderTicket acc="ACC00000001" market={MARKET} quotes={QUOTES} disabled={false} />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  expect(screen.getByText("Bid 1.09995 · Ask 1.10005")).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Volume (lots)"));
  await user.type(screen.getByLabelText("Volume (lots)"), "1.5");
  await user.type(screen.getByLabelText("Stop loss (optional)"), "1.095");
  await user.click(screen.getByRole("button", { name: "Buy 1.50 EUR/USD at market" }));
  expect(await screen.findByText("Filled at 1.10005.")).toBeInTheDocument();
  expect(calls[0]).toEqual({
    url: "/api/accounts/ACC00000001/orders",
    method: "POST",
    body: { symbol: "EUR/USD", side: "buy", type: "market", volume: 150, stop_loss: 109_500 },
  });
});

it("places a limit order with its price, and explains a refusal", async () => {
  const refused = { status: "rejected", events: [{ type: "OrderRejected", reason: "insufficient_margin" }] };
  const { calls, fetcher } = recorder(() => [200, refused]);
  renderWith(<OrderTicket acc="ACC00000001" market={MARKET} quotes={QUOTES} disabled={false} />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Symbol"), "USD/JPY");
  await user.selectOptions(screen.getByLabelText("Order type"), "limit");
  const sell = screen.getByRole("button", { name: "Sell 0.10 USD/JPY limit" });
  expect(sell).toBeDisabled(); // no price yet
  expect(screen.getByText("Price: a positive number with up to 3 decimals.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Price"), "150.25");
  await user.click(sell);
  expect(await screen.findByText("Not enough free margin for this order.")).toBeInTheDocument();
  expect(calls[0]?.body).toEqual({ symbol: "USD/JPY", side: "sell", type: "limit", volume: 10, level: 150_250 });
});

it("refuses to send what it cannot read exactly", async () => {
  const { calls, fetcher } = recorder(() => [200, {}]);
  renderWith(<OrderTicket acc="ACC00000001" market={MARKET} quotes={QUOTES} disabled={false} />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.clear(screen.getByLabelText("Volume (lots)"));
  await user.type(screen.getByLabelText("Volume (lots)"), "0.001");
  await user.type(screen.getByLabelText("Take profit (optional)"), "1.100001");
  expect(screen.getByText("Volume: lots in steps of 0.01, at least 0.01.")).toBeInTheDocument();
  expect(screen.getByText("Take profit: a positive number with up to 5 decimals.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Buy/ })).toBeDisabled();
  expect(calls).toEqual([]);
});

it("is closed for trading when the account cannot trade", () => {
  renderWith(<OrderTicket acc="ACC00000001" market={MARKET} quotes={QUOTES} disabled={true} />, fakeAuth(), vi.fn());
  expect(screen.getByRole("button", { name: /^Buy/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /^Sell/ })).toBeDisabled();
});

const POSITION: LivePosition = {
  position_id: "p1",
  symbol: "EUR/USD",
  side: "buy",
  volume: 100,
  open_price: 110_005,
  opened_at: 0,
  stop_loss: null,
  take_profit: null,
  current_price: 109_955,
  pnl_cents: -5_000,
};

function inTable(row: React.ReactNode) {
  return (
    <table>
      <tbody>{row}</tbody>
    </table>
  );
}

it("closes a position and changes its stops", async () => {
  const { calls, fetcher } = recorder((call) =>
    call.url.endsWith("/close")
      ? [200, { status: "done", events: [{ type: "PositionClosed" }] }]
      : [200, { status: "done", events: [{ type: "StopsModified" }] }],
  );
  renderWith(inTable(<PositionRow acc="ACC00000001" position={POSITION} digits={5} canTrade={true} />), fakeAuth(), fetcher);
  const row = screen.getByRole("row");
  expect(row).toHaveTextContent("1.09955-$50.00— / —");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Change stops of Buy 1.00 EUR/USD" }));
  await user.type(screen.getByLabelText("Stop loss for Buy 1.00 EUR/USD"), "1.09");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Stops changed.")).toBeInTheDocument();
  expect(calls[0]).toEqual({
    url: "/api/accounts/ACC00000001/positions/p1/stops",
    method: "PUT",
    body: { stop_loss: 109_000, take_profit: null },
  });
  await user.click(screen.getByRole("button", { name: "Close Buy 1.00 EUR/USD" }));
  await waitFor(() => {
    expect(calls[1]).toEqual({ url: "/api/accounts/ACC00000001/positions/p1/close", method: "POST", body: {} });
  });
  expect(await screen.findByText("Closed.")).toBeInTheDocument();
});

it("offers no actions on an account that cannot trade", () => {
  renderWith(inTable(<PositionRow acc="ACC00000001" position={POSITION} digits={5} canTrade={false} />), fakeAuth(), vi.fn());
  expect(within(screen.getByRole("row")).queryByRole("button")).toBeNull();
});

it("cancels a pending order", async () => {
  const order: LivePendingOrder = {
    request: { order_id: "o9", symbol: "EUR/USD", side: "sell", order_type: "limit", volume: 10, level: 111_000, stop_loss: null, take_profit: null },
    placed_at: 0,
  };
  const { calls, fetcher } = recorder(() => [200, { status: "done", events: [{ type: "OrderCancelled" }] }]);
  renderWith(inTable(<PendingRow acc="ACC00000001" order={order} digits={5} canTrade={true} />), fakeAuth(), fetcher);
  await userEvent.setup().click(screen.getByRole("button", { name: "Cancel limit sell 0.10 EUR/USD" }));
  expect(await screen.findByText("Order cancelled.")).toBeInTheDocument();
  expect(calls[0]).toMatchObject({ url: "/api/accounts/ACC00000001/orders/o9", method: "DELETE" });
});

it("describes each kind of outcome", () => {
  expect(describeOutcome({ status: "pending" }, 5).tone).toBe("info");
  expect(describeOutcome({ status: "done", events: [{ type: "OrderPlaced", level: 111_000 }] }, 5).text).toBe(
    "Order placed at 1.11000.",
  );
  expect(describeOutcome({ status: "done", events: [{ type: "OrderRejected", reason: "news_window" }] }, 5).tone).toBe("error");
});

it("exports closed trades as exact CSV", () => {
  const events: HistoryEvent[] = [
    { seq: 3, idx: 0, type: "PositionOpened", ts: 1 },
    {
      seq: 5,
      idx: 0,
      type: "PositionClosed",
      ts: Date.UTC(2026, 9, 30, 12, 0, 0),
      position_id: "p1",
      symbol: "EUR/USD",
      side: "buy",
      volume: 150,
      open_price: 110_005,
      close_price: 109_955,
      pnl_cents: -7_500,
      reason: "stop_loss",
    },
  ];
  const trades = tradesOf(events);
  expect(trades).toHaveLength(1);
  expect(tradesCsv(trades, () => 5)).toBe(
    "closed_utc,position,symbol,side,lots,open_price,close_price,profit_usd,reason\r\n" +
      "2026-10-30T12:00:00.000Z,p1,EUR/USD,buy,1.50,1.10005,1.09955,-75.00,stop_loss\r\n",
  );
});

it("lists restricted news windows", async () => {
  const news = [{ event_id: "us-nfp-2026-11-06", start_ts: 0, end_ts: 240_001, symbols: ["EUR/USD", "USD/JPY"] }];
  const { fetcher } = recorder(() => [200, news]);
  renderWith(<NewsPage />, fakeAuth(), fetcher);
  const row = (await screen.findByRole("rowheader", { name: "us-nfp-2026-11-06" })).closest("tr");
  expect(row).toHaveTextContent("EUR/USD, USD/JPY");
});
