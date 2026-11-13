import type { Stage } from "../api/types";

/** The maximum loss kind in words: "static", "trailing", "trailing at end of day". */
export function lossKindLabel(kind: Stage["max_loss_kind"]): string {
  if (kind === "trailing_eod") return "trailing at end of day";
  return kind;
}

/** How the maximum loss floor moves, in one sentence for the rules page. */
export function lossFloorText(r: Pick<Stage, "max_loss_kind" | "floor_locks_at_start" | "floor_restarts_after_payout">): string {
  if (r.max_loss_kind === "static") {
    return "the account is breached when equity falls to or below the starting balance minus the maximum loss.";
  }
  const follows =
    r.max_loss_kind === "trailing_eod"
      ? "the floor follows the highest balance at a 17:00 New York close, minus the maximum loss; it moves once a day and never down"
      : "the floor follows the highest equity reached, minus the maximum loss";
  const locks = r.floor_locks_at_start === false ? ", and keeps rising above the starting balance" : ", and never rises above the starting balance";
  const payout =
    r.floor_restarts_after_payout === false
      ? " A payout does not move the floor down again, so a payout must leave the account above it."
      : " After a payout the floor is set again from the new balance.";
  return `${follows}${locks}. Equity at or below the floor breaches the account at any time.${payout}`;
}
