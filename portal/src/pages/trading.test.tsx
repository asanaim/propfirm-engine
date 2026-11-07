import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LivePendingOrder, LivePosition, LiveTrailingOrder, Market } from "../api/types";
import { describeOutcome, OrderTicket } from "../components/OrderTicket";
import { PendingRow, PositionRow, TrailingRow } from "../components/PositionRow";
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

it("places a trailing sell with distance and step in whole points", async () => {
  const placed = { status: "done", events: [{ type: "TrailingPlaced", level: 109_845 }] };
  const { calls, fetcher } = recorder(() => [200, placed]);
  renderWith(<OrderTicket acc="ACC00000001" market={MARKET} quotes={QUOTES} disabled={false} />, fakeAuth(), fetcher);
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Order type"), "trailing");
  expect(screen.queryByLabelText("Stop loss (optional)")).toBeNull();
  const sell = screen.getByRole("button", { name: "Sell 0.10 EUR/USD trailing" });
  expect(sell).toBeDisabled();
  expect(screen.getByText("Distance: whole points, at least 1.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Distance (points)"), "150");
  await user.clear(screen.getByLabelText("Step (points)"));
  await user.type(screen.getByLabelText("Step (points)"), "1.5");
  expect(screen.getByText("Step: whole points, at least 1.")).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Step (points)"));
  await user.type(screen.getByLabelText("Step (points)"), "5");
  expect(screen.getByText(/150 points = 0\.00150\. A trailing buy starts at 1\.10155; a trailing sell at 1\.09845\./)).toBeInTheDocument();
  await user.click(sell);
  expect(await screen.findByText("Trailing order working at 1.09845.")).toBeInTheDocument();
  expect(calls[0]).toEqual({
    url: "/api/accounts/ACC00000001/trailing-orders",
    method: "POST",
    body: { symbol: "EUR/USD", side: "sell", volume: 10, distance: 150, step: 5 },
  });
});

const STOP: LiveTrailingOrder = {
  order_id: "t1",
  symbol: "EUR/USD",
  side: "buy",
  kind: "stop",
  volume: null,
  position_id: "p1",
  distance: 100,
  step: 10,
  level: 109_895,
  moved_at: Date.UTC(2026, 9, 30, 12, 0, 5),
  moved_seq: 42,
  placed_at: Date.UTC(2026, 9, 30, 12, 0, 0),
};

it("sets a position's trailing stop and shows its level", async () => {
  const { calls, fetcher } = recorder(() => [200, { status: "done", events: [{ type: "TrailingPlaced", level: 109_905 }] }]);
  renderWith(
    inTable(<PositionRow acc="ACC00000001" position={POSITION} digits={5} canTrade={true} trailing={STOP} />),
    fakeAuth(),
    fetcher,
  );
  expect(screen.getByRole("row")).toHaveTextContent("Trailing stop 1.09895");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Trailing stop of Buy 1.00 EUR/USD" }));
  const distance = screen.getByLabelText("Trailing distance in points for Buy 1.00 EUR/USD");
  expect(distance).toHaveValue("100");
  await user.clear(distance);
  await user.type(distance, "90");
  await user.click(screen.getByRole("button", { name: "Set trailing stop" }));
  expect(await screen.findByText("Trailing order working at 1.09905.")).toBeInTheDocument();
  expect(calls[0]).toEqual({
    url: "/api/accounts/ACC00000001/positions/p1/trailing-stop",
    method: "PUT",
    body: { distance: 90, step: 10 },
  });
});

it("lists a trailing order with its level and last move, and cancels it", async () => {
  const entry: LiveTrailingOrder = { ...STOP, order_id: "e1", kind: "entry", side: "sell", volume: 10, position_id: null };
  const { calls, fetcher } = recorder(() => [200, { status: "done", events: [{ type: "OrderCancelled" }] }]);
  renderWith(inTable(<TrailingRow acc="ACC00000001" order={entry} digits={5} canTrade={true} />), fakeAuth(), fetcher);
  const row = screen.getByRole("row");
  expect(row).toHaveTextContent("Trailing sell0.10100 / 101.09895last moved");
  expect(row).toHaveTextContent("(price update 42)");
  expect(within(row).getByText(/last moved/).closest("time")).toHaveAttribute("datetime", "2026-10-30T12:00:05.000Z");
  await userEvent.setup().click(screen.getByRole("button", { name: "Cancel Trailing sell 0.10 EUR/USD" }));
  expect(await screen.findByText("Order cancelled.")).toBeInTheDocument();
  expect(calls[0]).toMatchObject({ url: "/api/accounts/ACC00000001/orders/e1", method: "DELETE" });
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

it("lists restricted news windows by day, public, with the next one counted down", async () => {
  const soon = Date.now() + 3 * 3_600_000;
  const news = [
    { event_id: "us-emp", title: "US employment situation (October)", start_ts: soon, end_ts: soon + 240_001, release_ts: soon + 120_000, symbols: ["EUR/USD", "USD/JPY"], currency: "USD", source: "https://www.bls.gov/schedule/" },
    { event_id: "2026-11-07-snb", title: null, start_ts: soon + 86_400_000, end_ts: soon + 86_640_001, symbols: ["USD/CHF"] },
    { event_id: "over", title: "Already over", start_ts: 0, end_ts: 240_001, symbols: ["EUR/USD"] },
  ];
  const { fetcher, calls } = recorder(() => [200, news]);
  renderWith(<NewsPage />, fakeAuth({ status: "anonymous" }), fetcher);
  expect(await screen.findByText("Next restricted release")).toBeInTheDocument();
  expect(calls[0]?.url).toContain("/calendar");
  const item = screen.getAllByText("US employment situation (October)").at(-1)?.closest("li");
  expect(item).toHaveTextContent("USD");
  expect(item).toHaveTextContent("EUR/USD");
  expect(screen.getByRole("link", { name: /publisher/i })).toHaveAttribute("href", "https://www.bls.gov/schedule/");
  expect(screen.getByText("2026-11-07-snb")).toBeInTheDocument(); // no title: the id
  expect(screen.queryByText("Already over")).toBeNull();
  // one symbol at a time
  fireEvent.change(screen.getByRole("combobox", { name: "Symbol" }), { target: { value: "USD/CHF" } });
  expect(screen.queryByText("Already over")).toBeNull();
  expect(screen.getAllByText("2026-11-07-snb").length).toBeGreaterThan(0);
  expect(screen.queryAllByText("US employment situation (October)").every((n) => n.closest(".next-up") === null)).toBe(true);
});

it("marks impact and restricted releases, and filters them", async () => {
  const soon = Date.now() + 3 * 3_600_000;
  const news = [
    { event_id: "cpi", title: "US CPI y/y", start_ts: soon, end_ts: soon + 240_001, release_ts: soon + 120_000, symbols: ["EUR/USD"], impact: "high", restricted: true },
    { event_id: "retail", title: "US retail sales", start_ts: soon + 3_600_000, end_ts: soon + 3_600_000, release_ts: soon + 3_600_000, symbols: [], impact: "medium", restricted: false },
    { event_id: "ifo", title: "German Ifo", start_ts: soon + 7_200_000, end_ts: soon + 7_200_000, symbols: [], impact: "low", restricted: false },
  ];
  const { fetcher } = recorder(() => [200, news]);
  renderWith(<NewsPage />, fakeAuth({ status: "anonymous" }), fetcher);
  const retail = (await screen.findByText("US retail sales")).closest("li");
  expect(retail).not.toHaveClass("restricted");
  expect(retail).toHaveTextContent("Not restricted");
  expect(within(retail as HTMLElement).getByRole("img", { name: "Medium impact" })).toBeInTheDocument();
  const cpi = screen.getAllByText("US CPI y/y").map((n) => n.closest("li")).find((li) => li !== null);
  expect(cpi).toHaveClass("restricted");
  expect(cpi).toHaveTextContent("Restricted");
  fireEvent.click(screen.getByRole("checkbox", { name: /Low/ }));
  expect(screen.queryByText("German Ifo")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Restricted only" }));
  expect(screen.queryByText("US retail sales")).toBeNull();
  expect(screen.getAllByText("US CPI y/y").length).toBeGreaterThan(0);
});
