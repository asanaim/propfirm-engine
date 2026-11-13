import { describe, expect, it } from "vitest";
import { eventId, utcMs } from "./NewsEntry";

describe("adding a news event by hand", () => {
  it("reads the release time as UTC", () => {
    expect(utcMs("2026-11-20T13:30")).toBe(Date.UTC(2026, 10, 20, 13, 30));
    expect(utcMs("")).toBeNull();
    expect(utcMs("20/11/2026 13:30")).toBeNull();
  });
  it("makes an event id the engine accepts", () => {
    const id = eventId("US Consumer Prices (CPI)!", Date.UTC(2026, 10, 20, 13, 30));
    expect(id).toBe("us-consumer-prices-cpi-20261120-1330");
    expect(id).toMatch(/^[a-z0-9][a-z0-9-]{0,63}$/);
    expect(eventId("", null)).toBe("event-now");
    expect(eventId("x".repeat(200), 0).length).toBeLessThanOrEqual(64);
  });
});
