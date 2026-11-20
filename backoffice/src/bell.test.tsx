import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "./api";
import { BackOfficeContext } from "./context";
import { NotificationBell, type OpenNotification, panelItems } from "./NotificationBell";
import { recordPath } from "./places";

const OPEN: OpenNotification[] = [
  { id: 1, kind: "payout", ref: "PO00000007", title: "Payout to approve", body: "1,200.00 USD", urgent: false, ts: 1_700_000_000_000 },
  { id: 2, kind: "case", ref: "CAS00000003", title: "Chargeback opened", body: "", urgent: true, ts: 1_600_000_000_000 },
  { id: 3, kind: "note", ref: null, title: "Rule set published", body: "lite-s1-v4", urgent: false, ts: 1_750_000_000_000 },
];

function rig(post = vi.fn<(path: string, body: unknown) => Promise<unknown>>(() => Promise.resolve({}))) {
  const api: Api = {
    get: <T,>() => Promise.resolve(OPEN as T),
    post: <T,>(path: string, body?: unknown) => post(path, body) as Promise<T>,
    put: () => Promise.reject(new Error("no")),
  };
  render(
    <MemoryRouter>
      <BackOfficeContext.Provider value={{ api, manager: {} as UserManager }}>
        <button type="button">Behind</button>
        <NotificationBell />
      </BackOfficeContext.Provider>
    </MemoryRouter>,
  );
  return post;
}

describe("the notifications panel", () => {
  it("lists urgent first, then newest, up to the limit", () => {
    expect(panelItems(OPEN).map((n) => n.id)).toEqual([2, 3, 1]);
    expect(panelItems(OPEN, 2).map((n) => n.id)).toEqual([2, 3]);
  });

  it("links a record reference to its page", () => {
    expect(recordPath("PO00000007")).toBe("/payouts/PO00000007/show");
    expect(recordPath("CAS00000003")).toBe("/cases/CAS00000003/show");
    expect(recordPath("STF00000001")).toBeNull();
    expect(recordPath(null)).toBeNull();
  });

  it("opens a floating panel with no backdrop and keeps the page usable", async () => {
    rig();
    const bell = await screen.findByRole("button", { name: "Notifications, 3 open" });
    expect(bell).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(bell);
    const panel = await screen.findByRole("dialog", { name: "Open notifications" });
    expect(bell).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(document.querySelector(".MuiBackdrop-root")).toBeNull();
    expect(screen.getByRole("link", { name: "Payout to approve" })).toHaveAttribute("href", "/payouts/PO00000007/show");
    expect(screen.getByText("Rule set published").closest("a")).toBeNull();
    expect(screen.getByLabelText("urgent")).toBeInTheDocument();
    // the page behind is neither hidden nor blocked
    expect(screen.getByRole("button", { name: "Behind" })).not.toHaveAttribute("aria-hidden");
  });

  it("closes on Escape and on a click outside, returning focus to the bell", async () => {
    rig();
    const bell = await screen.findByRole("button", { name: "Notifications, 3 open" });
    await userEvent.click(bell);
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(bell).toHaveFocus();
    await userEvent.click(bell);
    await screen.findByRole("dialog");
    await userEvent.click(screen.getByRole("button", { name: "Behind" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("acknowledges a notification and drops it from the count", async () => {
    const post = rig();
    await userEvent.click(await screen.findByRole("button", { name: "Notifications, 3 open" }));
    await screen.findByRole("dialog");
    const ack = screen.getAllByRole("button", { name: "Acknowledge" })[0];
    if (ack === undefined) throw new Error("no acknowledge button");
    await userEvent.click(ack);
    expect(post).toHaveBeenCalledWith("/staff/notifications/2/ack", {});
    expect(await screen.findByRole("button", { name: "Notifications, 2 open" })).toBeInTheDocument();
    expect(screen.queryByText("Chargeback opened")).toBeNull();
  });
});
