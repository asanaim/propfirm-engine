import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FooterContact } from "./SiteStatus";

describe("the footer's contact details (set under General)", () => {
  it("shows the name, the address and the support address as a mail link", () => {
    render(<FooterContact contact={{ company_name: "Pipridge Ltd", address: "1 Market Street, London", support_email: "help@pipridge.example" }} />);
    expect(screen.getByText(/Pipridge Ltd, 1 Market Street, London/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "help@pipridge.example" })).toHaveAttribute("href", "mailto:help@pipridge.example");
  });
  it("shows nothing when none is set", () => {
    const { container } = render(<FooterContact contact={{ company_name: "", address: "", support_email: "" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
