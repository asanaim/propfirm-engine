
describe("short holds on the payout page", () => {
  it("says what share of profit came from trades closed within seconds", async () => {
    const { shortHoldText } = await import("./resources/payouts");
    const v = { seconds: 60, trades: 40, short_trades: 3, profit_cents: 80_000, short_profit_cents: 50_000, share_basis_points: 6250 };
    expect(shortHoldText(v)).toBe("62.50% of profit from 3 of 40 trades closed by the trader within 60 s");
    expect(shortHoldText({ ...v, profit_cents: 0 })).toBe("No profit this period (40 trades)");
    expect(shortHoldText(null)).toBe("-");
  });
});
