import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { ResetPage, SimulatorPage, SymbolsPage } from "./ToolsExtra";

const LIST = [
  { symbol: "EUR/USD", asset_class: "forex", digits: 5, contract_size: 100_000, leverage: "100/1", quote_currency: "USD", pip: "0.0001", pip_name: "pip", commission_percent: "0" },
  { symbol: "BTC/USD", asset_class: "crypto", digits: 2, contract_size: 1, leverage: "333/100", quote_currency: "USD", pip: "0.01", pip_name: "point", commission_percent: "0.0525" },
];
const anon = () => fakeAuth({ status: "anonymous", session: null });

it("lists the symbols by class, and finds one by search", async () => {
  renderWith(<SymbolsPage />, anon(), fakeFetch([["GET", "/tools/instruments", 200, LIST]]));
  expect(await screen.findByRole("rowheader", { name: "EUR/USD" })).toBeInTheDocument();
  expect(screen.queryByRole("rowheader", { name: "BTC/USD" })).toBeNull();
  await userEvent.type(screen.getByLabelText("Search a symbol"), "btc");
  expect(screen.getByRole("rowheader", { name: "BTC/USD" }).closest("tr")).toHaveTextContent("1:3.330.0525 % of the trade");
});

it("shows the next reset and the time left", () => {
  renderWith(<ResetPage now={Date.UTC(2026, 10, 10, 17, 16, 53)} />, anon(), fakeFetch([]));
  expect(screen.getByText("4:43:07")).toBeInTheDocument(); // to 22:00 UTC (17:00 New York, winter)
});

it("simulates curves from the inputs", async () => {
  renderWith(<SimulatorPage />, anon(), fakeFetch([]));
  expect(screen.getByRole("img", { name: "10 simulated equity curves" })).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText("Win rate (%)"));
  expect(screen.getByText("Fill in every field with a number.")).toBeInTheDocument();
});
