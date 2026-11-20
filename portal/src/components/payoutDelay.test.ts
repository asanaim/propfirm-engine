import { describe, expect, it } from "vitest";
import { payoutDelayText } from "./SiteStatus";

describe("the payout delay notice (D-14.23)", () => {
  it("says since when, and that nothing needs doing again", () => {
    expect(payoutDelayText(Date.UTC(2026, 10, 20, 9))).toBe(
      "Payouts are taking longer than usual since 2026-11-20. Every approved payout will be paid, in the order it was requested; you do not need to request again.",
    );
    expect(payoutDelayText(null)).toMatch(/^Payouts are taking longer than usual\. /);
  });
});
