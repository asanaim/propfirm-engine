import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminContext, testDataProvider } from "react-admin";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { isAt, MenuItems } from "./Menu";
import { GROUPS, PLACES } from "./places";
import { screensFor } from "./screens";

const OWNER = screensFor([
  "staff.view",
  "staff.directory",
  "settings.view",
  "settings.change",
  "news.desk",
  "payout.pass",
  "case.view",
]);

function menuAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AdminContext dataProvider={testDataProvider()}>
        <MenuItems screens={OWNER} />
      </AdminContext>
    </MemoryRouter>,
  );
}

function section(label: string): HTMLElement {
  return screen.getByRole("button", { name: label });
}

describe("the menu in sections (rule AC28)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("places every item in a known section or at the top", () => {
    const keys = new Set<string>(GROUPS.map((g) => g.key));
    for (const p of PLACES) if (p.group !== null) expect(keys).toContain(p.group);
    expect(PLACES.filter((p) => p.group === null).map((p) => p.label)).toEqual(["My work", "Notifications"]);
  });

  it("opens the section of the page in view and keeps the others closed", () => {
    menuAt("/payouts");
    expect(section("Money")).toHaveAttribute("aria-expanded", "true");
    expect(section("Settings")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("My work")).toBeVisible();
  });

  it("opens and closes a section, and remembers it", async () => {
    const first = menuAt("/");
    await userEvent.click(section("Settings"));
    expect(section("Settings")).toHaveAttribute("aria-expanded", "true");
    expect(within(document.getElementById("menu-group-settings") as HTMLElement).getByText("System settings")).toBeInTheDocument();
    first.unmount();
    menuAt("/");
    expect(section("Settings")).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(section("Settings"));
    expect(section("Settings")).toHaveAttribute("aria-expanded", "false");
  });

  it("matches a page and the pages below it", () => {
    expect(isAt("/payouts/PO00000001/show", "/payouts")).toBe(true);
    expect(isAt("/payouts", "/payouts")).toBe(true);
    expect(isAt("/payout-methods", "/payouts")).toBe(false);
    expect(isAt("/traders", "/")).toBe(false);
    expect(isAt("/", "/")).toBe(true);
  });
});
