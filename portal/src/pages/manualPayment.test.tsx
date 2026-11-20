import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PlacedOrder } from "../api/types";
import { ManualPayment } from "./ShopPage";

describe("a method paid by hand (D-14.24)", () => {
  it("shows the instructions as written and the order as the reference", () => {
    const order = { ord: "ORD00000002", price_cents: 10_900, pay_url: "", pay_instructions: "IBAN XX00 0000\nBank: Example", pay_reference: "ORD00000002" } as PlacedOrder;
    render(<ManualPayment order={order} />);
    expect(screen.getByText(/Pay \$109\.00 as follows/)).toBeInTheDocument();
    expect(screen.getByText(/IBAN XX00 0000/)).toBeInTheDocument();
    expect(screen.getByText("ORD00000002")).toBeInTheDocument();
    expect(screen.getByText(/once we have seen the payment/)).toBeInTheDocument();
  });
});
