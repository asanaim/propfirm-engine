import type { Stage } from "../api/types";
import { useAccountStream } from "../api/useAccountStream";
import { Problem } from "../components/Problem";
import { Card, Loading } from "../components/ui";
import { formatCents, formatPercent } from "../lib/money";
import { Link } from "../router";
import { AccountTabs } from "./AccountPage";

const STAGES: Record<Stage["stage"], string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };

/** The account's own rule set (its version never changes while it is
open), with each limit in dollars, and how the engine applies them. */
export function RulesPage({ acc }: { acc: string }) {
  const live = useAccountStream(acc);
  if (live.error !== null) return <Problem error={live.error} />;
  const a = live.account;
  if (a === null) return <Loading />;
  const r = a.rules;
  return (
    <div className="page narrow">
      <p className="crumbs">
        <Link href="/accounts">Accounts</Link> / {acc}
      </p>
      <AccountTabs acc={acc} current="rules" />
      <h1>Rules</h1>
      <p className="muted">
        {STAGES[r.stage]}, rule set {r.rsv_id}. These rules are fixed for this account while it is open.
      </p>
      <Card title="Limits">
        <dl className="facts">
          {a.target_balance_cents !== null && (
            <>
              <dt>Profit target</dt>
              <dd>
                {formatPercent(r.profit_target_percent)}: {r.target_basis === "equity" ? "equity" : "closed balance"} of{" "}
                {formatCents(a.target_balance_cents)}
              </dd>
            </>
          )}
          <dt>Daily loss limit</dt>
          <dd>
            {formatPercent(r.daily_loss_percent)}: {formatCents(a.daily_loss_limit_cents)}
          </dd>
          <dt>Maximum loss</dt>
          <dd>
            {formatPercent(r.max_loss_percent)} ({r.max_loss_kind}): {formatCents(a.max_loss_limit_cents)}
          </dd>
          <dt>Minimum trading days</dt>
          <dd>{r.min_trading_days}</dd>
          <dt>Consistency</dt>
          <dd>{r.consistency_percent === null ? "none" : `best day at most ${formatPercent(r.consistency_percent)} of profit`}</dd>
          <dt>Time limit</dt>
          <dd>none</dd>
        </dl>
      </Card>
      <Card title="How they are measured">
        <ul className="rules">
          <li>
            A trading day runs from 17:00 to 17:00 New York time. At 17:00 the day&apos;s starting level is set to the
            higher of the balance and the equity at that moment.
          </li>
          <li>
            Daily loss: the account is breached when equity (open profit and loss included) falls to or below the
            day&apos;s starting level minus the daily limit. A floating loss of the daily limit or more is breached at
            17:00 itself.
          </li>
          <li>
            Maximum loss ({r.max_loss_kind}):{" "}
            {r.max_loss_kind === "static"
              ? "the account is breached when equity falls to or below the starting balance minus the maximum loss."
              : "the floor follows the highest equity reached, minus the maximum loss, and never rises above the starting balance."}
          </li>
          {a.target_balance_cents !== null && (
            <li>
              Passing: {r.target_basis === "equity" ? "equity" : "a closed balance"} at or above the target, with at
              least {r.min_trading_days} trading days (a day counts when a trade opens on it)
              {r.flat_to_pass ? ", and no open position or pending order" : ""}
              {r.consistency_at_pass && r.consistency_percent !== null
                ? `, and the best day at most ${formatPercent(r.consistency_percent)} of the profit`
                : ""}
              .
            </li>
          )}
          <li>
            No trade opens or closes on the affected symbols around listed news events: see the{" "}
            <Link href="/news">news calendar</Link>.
          </li>
          <li>
            When a breach and a pass happen on the same price, the {r.tie_rule === "pass_wins" ? "pass" : "breach"}{" "}
            counts.
          </li>
        </ul>
      </Card>
    </div>
  );
}
