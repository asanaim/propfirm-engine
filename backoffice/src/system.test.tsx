import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { screensFor } from "./screens";
import { SystemView, type SystemInfo } from "./System";

const INFO: SystemInfo = {
  application: { name: "pipridge engine", version: "0.1.0", revision: "44e1a3f", python: "3.15.2", api_started_at: Date.UTC(2026, 10, 12, 8, 5) },
  parts: [
    { name: "PostgreSQL", status: "ok", version: "PostgreSQL 18.6" },
    { name: "Valkey", status: "unavailable", version: "ConnectionRefusedError" },
  ],
  journal: { seq: 42, engine_ts: null },
};

describe("system information (rule AC23)", () => {
  it("shows versions and which parts answer", () => {
    render(<SystemView info={INFO} />);
    expect(screen.getByText("pipridge engine 0.1.0")).toBeInTheDocument();
    expect(screen.getByText("44e1a3f")).toBeInTheDocument();
    expect(screen.getByText("2026-11-12 08:05 UTC")).toBeInTheDocument();
    expect(screen.getByText("event 42")).toBeInTheDocument();
    expect(screen.getByText("answering")).toBeInTheDocument();
    expect(screen.getByText("not answering")).toBeInTheDocument();
  });

  it("is offered only to logins that change settings", () => {
    expect(screensFor(["settings.view", "settings.change"]).has("system")).toBe(true);
    expect(screensFor(["staff.view", "settings.view"]).has("system")).toBe(false);
  });
});
