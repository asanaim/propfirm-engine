import { useEffect, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { Product } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Card, Loading, Notice } from "../components/ui";
import { Ridge } from "../components/Ridge";
import { formatCents, formatPercent } from "../lib/money";
import { Link } from "../router";
import { RegisterForm } from "./RegisterForm";

export function OverviewPage() {
  const auth = useAuth();
  const me = useMe();
  if (auth.status === "loading" || me.status === "loading") return <Loading />;
  if (me.status === "anonymous") return <Landing onSignIn={() => void auth.signIn("/")} />;
  if (me.status === "unregistered") {
    return (
      <div className="page narrow">
        <h1>Welcome</h1>
        <RegisterForm onDone={me.reload} />
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const { full_name, accounts, orders } = me.me;
  const pending = orders.filter((o) => o.status === "pending").length;
  return (
    <div className="page">
      <h1>Hello, {full_name}</h1>
      <div className="grid">
        <Card title="Accounts" actions={<Link href="/accounts">View accounts</Link>}>
          <p className="figure">{accounts.length}</p>
          <p className="muted">trading {accounts.length === 1 ? "account" : "accounts"}</p>
        </Card>
        <Card title="Orders" actions={<Link href="/shop">New challenge</Link>}>
          <p className="figure">{orders.length}</p>
          <p className="muted">{pending > 0 ? `${pending} awaiting payment` : "all settled"}</p>
        </Card>
        <Card title="Security" actions={<Link href="/security">Security settings</Link>}>
          <p>{auth.session?.mfa ? "Signed in with two-factor authentication." : "Two-factor authentication is off for this sign-in."}</p>
        </Card>
      </div>
    </div>
  );
}

const STEPS: { title: string; text: string }[] = [
  { title: "Choose a challenge", text: "Pick the evaluation and account size that suit how you trade." },
  { title: "Pass the evaluation", text: "Reach the profit target without breaking the daily or maximum loss." },
  { title: "Trade a funded account", text: "Same rules, simulated capital, no evaluation target." },
  { title: "Get paid", text: "Request a payout of your share of the profit." },
];

function cheapest(p: Product): number | undefined {
  const prices = p.sizes.map((s) => s.price_cents);
  return prices.length === 0 ? undefined : Math.min(...prices);
}

/** The signed-out home page: what a challenge is, what it costs, how it works. */
function Landing({ onSignIn }: { onSignIn: () => void }) {
  const api = useApi();
  const [products, setProducts] = useState<Product[] | null>(null);
  useEffect(() => {
    api.get<Product[]>("/catalogue").then(setProducts, () => {
      setProducts([]);
    });
  }, [api]);
  const first = products?.[0]?.stages[0];
  const split = products?.reduce((best, p) => Math.max(best, Number(p.profit_split_percent)), 0);
  return (
    <div className="landing">
      <section className="hero" aria-labelledby="hero-title">
        <h1 id="hero-title">
          Trade the evaluation.
          <br />
          Keep {split === undefined || split === 0 ? "most" : `${split.toString()} %`} of the profit.
        </h1>
        <p className="lead">
          Prove your trading on a simulated account. Pass, and trade a funded account with a share of every profit.
        </p>
        <div className="row">
          <Link className="button button-primary button-large" href="/shop">
            Choose a challenge
          </Link>
          <button type="button" className="button button-secondary button-large" onClick={onSignIn}>
            Sign in
          </button>
        </div>
        <Ridge
          target={first?.profit_target_percent === null || first === undefined ? undefined : formatPercent(first.profit_target_percent)}
          maxLoss={first === undefined ? undefined : formatPercent(first.max_loss_percent)}
        />
      </section>

      {products !== null && products.length > 0 && (
        <section className="tiles" aria-labelledby="tiles-title">
          <h2 id="tiles-title">Challenges</h2>
          <ul className="tile-list">
            {products.map((p) => {
              const from = cheapest(p);
              const evaluations = p.stages.filter((st) => st.stage !== "funded").length;
              return (
                <li key={p.id} className="tile">
                  <h3>{p.name}</h3>
                  <p className="tile-split">{p.profit_split_percent} % profit split</p>
                  <p className="muted">
                    {evaluations === 0
                      ? "Funded from the start"
                      : evaluations === 1
                        ? "One evaluation stage"
                        : `${evaluations.toString()} evaluation stages`}
                  </p>
                  <p className="tile-price">{from === undefined ? "" : `From ${formatCents(from)}`}</p>
                  <Link className="button button-primary" href="/shop">
                    Choose {p.name}
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="how" aria-labelledby="how-title">
        <h2 id="how-title">How it works</h2>
        <ol className="how-steps">
          {STEPS.map((s) => (
            <li key={s.title}>
              <h3>{s.title}</h3>
              <p className="muted">{s.text}</p>
            </li>
          ))}
        </ol>
        <p className="muted small">All trading here is simulated. Each challenge's rules are shown before you buy.</p>
      </section>
    </div>
  );
}
