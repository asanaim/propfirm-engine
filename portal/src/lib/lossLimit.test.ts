import { describe, expect, it } from "vitest";
import { lossFloorText, lossKindLabel } from "./lossLimit";

describe("loss limit words", () => {
  it("names each kind", () => {
    expect(lossKindLabel("static")).toBe("static");
    expect(lossKindLabel("trailing")).toBe("trailing");
    expect(lossKindLabel("trailing_eod")).toBe("trailing at end of day");
  });

  it("describes the end-of-day floor, the lock and the payout switch", () => {
    const eod = lossFloorText({ max_loss_kind: "trailing_eod", floor_locks_at_start: true, floor_restarts_after_payout: false });
    expect(eod).toContain("17:00 New York close");
    expect(eod).toContain("never rises above the starting balance");
    expect(eod).toContain("must leave the account above it");
    const open = lossFloorText({ max_loss_kind: "trailing", floor_locks_at_start: false, floor_restarts_after_payout: true });
    expect(open).toContain("highest equity");
    expect(open).toContain("keeps rising above the starting balance");
    expect(lossFloorText({ max_loss_kind: "static" })).toContain("starting balance minus the maximum loss");
  });
});
