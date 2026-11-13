import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminContext, testDataProvider } from "react-admin";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { CommandSearch, isOpenKey } from "./CommandSearch";
import { ICONS } from "./icons";
import { AREAS, PLACES, canOpen, filterAreas, recordHit, score, search } from "./places";
import { screensFor } from "./screens";
import { SettingsHub } from "./SettingsHub";

const ADMIN = screensFor(["staff.view", "staff.directory", "settings.view", "settings.change", "news.desk"]);
const APPRENTICE = screensFor(["staff.view_masked", "apprentice.propose"]);

describe("the catalogue of places", () => {
  it("holds every menu item, resource and custom route once", () => {
    const paths = new Set(PLACES.map((p) => p.path));
    expect(paths.size).toBe(PLACES.length);
    const menu = readFileSync(resolve(__dirname, "Menu.tsx"), "utf8");
    for (const m of menu.matchAll(/to="([^"]+)"/g)) expect(paths).toContain(m[1]);
    const app = readFileSync(resolve(__dirname, "App.tsx"), "utf8");
    for (const m of app.matchAll(/<Resource name="([^"]+)"/g)) expect(paths).toContain(`/${m[1]}`);
    for (const m of app.matchAll(/<Route path="([^"]+)"/g)) {
      const path = m[1] ?? "";
      expect(paths.has(path) || AREAS.some((a) => a.path === path)).toBe(true);
    }
  });

  it("names only icons that exist", () => {
    for (const p of PLACES) expect(ICONS[p.icon]).toBeDefined();
    for (const a of AREAS) expect(ICONS[a.icon]).toBeDefined();
  });
});

describe("the settings hub areas (rule AC05)", () => {
  it("has a card for every area the rule lists", () => {
    const keys = AREAS.map((a) => a.key);
    for (const k of ["general", "brand", "notifications", "payment", "withdrawal", "kyc", "seo", "languages", "extensions", "maintenance", "security"]) {
      expect(keys).toContain(k);
    }
  });

  it("links an open area, and names the rule of a planned one", () => {
    for (const a of AREAS) {
      if (a.state === "open") expect(a.path).not.toBeNull();
      if (a.state === "planned") expect(a.rule).toMatch(/^AC[0-9]{2}$/);
      if (a.state !== "open") expect(a.path).toBeNull();
    }
  });

  it("filters cards by any word of their title, keywords or text", () => {
    expect(filterAreas("").length).toBe(AREAS.length);
    expect(filterAreas("mfa").map((a) => a.key)).toEqual(["security"]);
    expect(filterAreas("SEO").map((a) => a.key)).toContain("seo");
    expect(filterAreas("zzz")).toEqual([]);
  });

  it("opens an area only for a login that may reach its screen", () => {
    const methods = AREAS.find((a) => a.key === "payment");
    expect(methods).toBeDefined();
    if (methods === undefined) return;
    expect(canOpen(methods, ADMIN)).toBe(true);
    expect(canOpen(methods, APPRENTICE)).toBe(false);
  });
});

describe("Ctrl+K search (rule AC06)", () => {
  it("matches the start of words, label words first", () => {
    expect(score("pay", "Payouts", "")).toBe(-0.5);
    expect(score("with", "Payouts", "withdrawals")).toBe(1);
    expect(score("out", "Payouts", "")).toBeNull();
    expect(score("   ", "Payouts", "")).toBeNull();
  });

  it("finds pages and settings, best first, without duplicates", () => {
    const hits = search("pay", ADMIN);
    expect(hits[0]?.label).toBe("Payouts");
    const labels = hits.map((h) => `${h.label}|${h.path}`);
    expect(new Set(labels).size).toBe(labels.length);
    expect(search("security", ADMIN).map((h) => h.path)).toContain("/security");
  });

  it("offers nothing a login may not open", () => {
    const hits = search("payment", APPRENTICE);
    expect(hits.map((h) => h.path)).not.toContain("/payment-methods");
    expect(search("staff", APPRENTICE).map((h) => h.path)).not.toContain("/members");
  });

  it("opens a full record reference, in any case", () => {
    expect(recordHit("po00000042", ADMIN)).toEqual({ label: "Payout PO00000042", group: "Record", path: "/payouts/PO00000042/show", icon: "payouts" });
    expect(recordHit(" CAS12345678 ", ADMIN)?.path).toBe("/cases/CAS12345678/show");
    expect(recordHit("ONB00000001", ADMIN)?.path).toBe("/onboardings/ONB00000001/show");
    expect(recordHit("PRG00000001", ADMIN)?.path).toBe("/progressions/PRG00000001/show");
    expect(recordHit("CIF00000007", ADMIN)?.path).toBe(`/cases?filter=${encodeURIComponent('{"cif":"CIF00000007"}')}`);
    expect(recordHit("PO123", ADMIN)).toBeNull();
    expect(recordHit("XX00000001", ADMIN)).toBeNull();
    expect(recordHit("PO00000042", screensFor([]))).toBeNull();
    expect(search("PO00000042", ADMIN)[0]?.group).toBe("Record");
  });

  it("opens on Ctrl+K and Cmd+K only", () => {
    const k = { key: "k", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false };
    expect(isOpenKey({ ...k, ctrlKey: true })).toBe(true);
    expect(isOpenKey({ ...k, metaKey: true, key: "K" })).toBe(true);
    expect(isOpenKey(k)).toBe(false);
    expect(isOpenKey({ ...k, ctrlKey: true, shiftKey: true })).toBe(false);
    expect(isOpenKey({ ...k, ctrlKey: true, key: "j" })).toBe(false);
  });
});

function Where() {
  const at = useLocation();
  return <div data-testid="where">{at.pathname}</div>;
}

const withPermissions = (permissions: string[]) => ({
  ...{
    login: () => Promise.resolve(),
    logout: () => Promise.resolve(),
    checkAuth: () => Promise.resolve(),
    checkError: () => Promise.resolve(),
  },
  getPermissions: () => Promise.resolve(permissions),
});

describe("the screens", () => {
  it("Ctrl+K opens the search and Enter goes to the first hit", async () => {
    render(
      <AdminContext dataProvider={testDataProvider()} authProvider={withPermissions(["staff.view"])}>
        <CommandSearch />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </AdminContext>,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const box = await screen.findByLabelText("Search the back office");
    await userEvent.type(box, "payo");
    expect(within(screen.getByRole("listbox")).getAllByRole("option")[0]).toHaveTextContent("Payouts");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/payouts");
  });

  it("the hub shows every card and its own search narrows them", async () => {
    render(
      <AdminContext dataProvider={testDataProvider()} authProvider={withPermissions(["settings.view"])}>
        <SettingsHub />
      </AdminContext>,
    );
    expect(await screen.findByTestId("area-languages")).toHaveTextContent("Not built yet (AC18)");
    await waitFor(() => {
      expect(screen.getByTestId("area-general")).toHaveTextContent("Open"); // once permissions load
    });
    await userEvent.type(screen.getByLabelText("Search settings"), "maintenance");
    expect(screen.queryByTestId("area-general")).toBeNull();
    expect(screen.getByTestId("area-maintenance")).toBeInTheDocument();
  });
});
