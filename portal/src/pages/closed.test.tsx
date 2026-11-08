import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClosedPage, MaintenanceBanner, OPEN } from "../components/SiteStatus";
import { fakeAuth, fakeFetch, renderWith } from "../test-utils";
import { OverviewPage } from "./OverviewPage";

const DOWN = { maintenance: true, registration_open: true, registering: false, selling: false };
const CLOSED = { maintenance: false, registration_open: false, registering: false, selling: true };

describe("maintenance and registration closed (rule AC17)", () => {
  it("shows the approved words and image", () => {
    render(<ClosedPage kind="registration_closed" page={{ fields: { title: "Closed", message: "Back in spring.", image: "/brand/closed.svg" }, items: [] }} />);
    expect(screen.getByRole("heading", { name: "Closed" })).toBeInTheDocument();
    expect(screen.getByText("Back in spring.")).toBeInTheDocument();
    expect(document.querySelector("img.closed-image")?.getAttribute("src")).toBe("/brand/closed.svg");
  });

  it("falls back to its own words, without an image", () => {
    render(<ClosedPage kind="maintenance" page={{ fields: { title: "Back soon", message: "Upgrading.", image: "" }, items: [] }} />);
    expect(document.querySelector("img")).toBeNull();
    render(<ClosedPage kind="maintenance" page={undefined} />);
    expect(screen.getByRole("heading", { name: "We are doing some maintenance" })).toBeInTheDocument();
  });

  it("shows the banner only in maintenance", () => {
    const { rerender } = render(<MaintenanceBanner status={OPEN} />);
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<MaintenanceBanner status={{ ...OPEN, maintenance: true, registering: false, selling: false }} />);
    expect(screen.getByRole("status")).toHaveTextContent("trading and payouts work as usual");
  });
});

describe("the home page while closed", () => {
  it("shows a signed out visitor the maintenance page, with sign in", async () => {
    const fetcher = fakeFetch([
      ["GET", "/site/status", 200, DOWN],
      ["GET", "/site", 200, { maintenance: { fields: { title: "Upgrading tonight", message: "Back by 02:00.", image: "" }, items: [] } }],
      ["GET", "/catalogue", 200, []],
    ]);
    renderWith(<OverviewPage />, fakeAuth({ status: "anonymous", session: null }), fetcher);
    expect(await screen.findByRole("heading", { name: "Upgrading tonight" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows a new trader the closed page instead of the registration form", async () => {
    const fetcher = fakeFetch([
      ["GET", "/me", 404, { detail: "not registered" }],
      ["GET", "/site/status", 200, CLOSED],
      ["GET", "/site", 200, {}],
    ]);
    renderWith(<OverviewPage />, fakeAuth(), fetcher);
    expect(await screen.findByRole("heading", { name: "Registration is closed for now" })).toBeInTheDocument();
    expect(screen.queryByText("Create your trader profile")).toBeNull();
  });
});
