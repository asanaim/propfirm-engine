import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { ToolsPage } from "./ToolsPage";

const LIST = [
  { symbol: "EUR/USD", asset_class: "forex", digits: 5, contract_size: 100_000, leverage: "100/1", quote_currency: "USD", pip: "0.0001", pip_name: "pip" },
  { symbol: "EUR/GBP", asset_class: "forex", digits: 5, contract_size: 100_000, leverage: "100/1", quote_currency: "GBP", pip: "0.0001", pip_name: "pip" },
];

it("sizes a trade for anyone, signed in or not, and asks for the rate a cross needs", async () => {
  renderWith(<ToolsPage />, fakeAuth({ status: "anonymous", session: null }), fakeFetch([["GET", "/tools/instruments", 200, LIST]]));
  expect(await screen.findByText("5.00 lots (risk $1,000.00)")).toBeInTheDocument(); // 100,000 x 1 % / (20 x $10)
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText(/^Symbol/), "EUR/GBP");
  expect(screen.getByText("Fill in the fields")).toBeInTheDocument();
  await user.type(screen.getByLabelText(/^1 GBP in USD/), "1.25");
  expect(screen.getByText("4.00 lots (risk $1,000.00)")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Margin" }));
  await user.type(screen.getByLabelText("Current price of EUR/GBP"), "0.86");
  expect(screen.getByText("$1,075.00")).toBeInTheDocument(); // 100,000 x 0.86 x 1.25 / 100
});
