import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { AdminContext, testDataProvider } from "react-admin";
import { describe, expect, it } from "vitest";
import { Login } from "./Login";
import { Menu } from "./Menu";

const read = (path: string) => readFileSync(resolve(__dirname, path), "utf8");

describe("the pipridge look in the back office", () => {
  it("uses the portal's own terrain and motion, unchanged", () => {
    expect(read("components/Terrain.tsx")).toBe(read("../../portal/src/components/Terrain.tsx"));
    expect(read("motion.ts")).toBe(read("../../portal/src/lib/motion.ts"));
  });

  it("moves only the item under the pointer, never the whole menu", () => {
    const css = read("backoffice.css");
    const plane = css.slice(css.indexOf(".menu-3d-plane {"), css.indexOf("}", css.indexOf(".menu-3d-plane {")));
    expect(plane).not.toMatch(/rotate|--rx|--ry/);
    expect(read("Menu.tsx")).not.toContain("useTilt");
  });

  it("keeps the menu still for people who prefer reduced motion", () => {
    const css = read("backoffice.css");
    const still = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    for (const selector of [".menu-3d .RaMenuItemLink-root", ".menu-3d-plane", ".login-card"]) expect(still).toContain(selector);
  });

  it("shows the sign-in card, falling back to flat ridges without WebGL", () => {
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <Login />
      </AdminContext>,
    );
    expect(screen.getByRole("heading", { name: "Back office" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(document.querySelector(".menu-ridge-fallback")).not.toBeNull();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("puts the menu on its 3D plane", async () => {
    render(
      <AdminContext dataProvider={testDataProvider()}>
        <Menu />
      </AdminContext>,
    );
    expect(await screen.findByText("My work")).toBeInTheDocument();
    expect(document.querySelector(".menu-3d .menu-3d-plane")).not.toBeNull();
  });
});
