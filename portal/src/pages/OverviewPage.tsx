import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { AccountRecord, LiveAccount, Me, Product } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Card, Loading, Notice } from "../components/ui";
import { Ridge } from "../components/Ridge";
import { Terrain } from "../components/Terrain";
import { formatCents, formatPercent, formatSize } from "../lib/money";
import { Ring } from "../components/Gauges";
import { countdown, ImpactMark, type NewsEvent } from "./NewsPage";
import { useReveal, useTilt } from "../lib/motion";
import { Link } from "../router";
import { RegisterForm } from "./RegisterForm";
import { ClosedPage, useSiteStatus } from "../components/SiteStatus";

export function OverviewPage() {
  const auth = useAuth();
  const me = useMe();
  const site = useSiteStatus();
  if (auth.status === "loading" || me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    if (site.status.maintenance) {
      return (
        <div className="page narrow">
          <ClosedPage kind="maintenance" page={site.pages.maintenance} />
          <button type="button" className="button button-secondary" onClick={() => void auth.signIn("/")}>
            Sign in
          </button>
        </div>
      );
    }
    return <Landing onSignIn={() => void auth.signIn("/")} onSignUp={() => void auth.signUp("/")} registering={site.status.registering} />;
  }
  if (me.status === "unregistered") {
    if (!site.status.registering) {
      // rule AC17: no new traders while closed or under maintenance
      const kind = site.status.maintenance ? "maintenance" : "registration_closed";
      return (
        <div className="page narrow">
          <ClosedPage kind={kind} page={site.pages[kind]} />
        </div>
      );
    }
    return (
      <div className="page narrow">
        <h1>Welcome</h1>
        <RegisterForm onDone={me.reload} />
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  return <Home me={me.me} mfa={auth.session?.mfa ?? false} />;
}

type Step = { text: string; href: string; action: string };

/** The one thing to do next, most urgent first. */
export function nextStep(me: Me, live: Record<string, LiveAccount | undefined>): Step {
  const pending = me.orders.find((o) => o.status === "pending");
  if (pending) return { text: `Your ${formatSize(pending.size_cents)} ${pending.product_id} order is waiting for payment.`, href: "/shop", action: "Complete payment" };
  const onboarding = me.onboardings.find((o) => ["verification", "verification_declined", "contract", "signature"].includes(o.status));
  if (onboarding) return { text: "You passed. Finish your funded onboarding to get your funded account.", href: "/funded", action: "Continue onboarding" };
  const passed = me.accounts.find((a) => live[a.acc]?.status === "passed");
  if (passed) return { text: `${passed.acc} passed its stage. Your next account is being prepared.`, href: "/accounts", action: "See accounts" };
  const active = me.accounts.map((a) => live[a.acc]).find((l) => l?.status === "active");
  if (active) {
    const target = active.target_balance_cents;
    const text =
      target === null
        ? `${active.account_id} is funded: trade within the loss limits and request a payout of your share.`
        : `${active.account_id}: ${formatCents(Math.max(0, target - active.balance_cents))} to the profit target.`;
    return { text, href: `/accounts/${active.account_id}`, action: "Open the account" };
  }
  return { text: "Start with a challenge: pass the evaluation and trade a funded account.", href: "/shop", action: "Choose a challenge" };
}

const STAGE_NAMES: Record<string, string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };

function AccountTile({ record, live }: { record: AccountRecord; live: LiveAccount | undefined }) {
  if (live === undefined) {
    return (
      <li className="acct">
        <p className="acct-head">
          <strong>{record.acc}</strong>
        </p>
        <Loading label="Connecting" />
      </li>
    );
  }
  const target = live.target_balance_cents;
  const initial = live.initial_balance_cents;
  const progress = target !== null && target > initial ? (live.balance_cents - initial) / (target - initial) : 0;
  const room = live.max_loss_limit_cents > 0 ? (live.equity_cents - live.max_floor_cents) / live.max_loss_limit_cents : 0;
  const roomTone = room > 0.5 ? "ok" : room > 0.2 ? "warn" : "danger";
  return (
    <li className="acct">
      <p className="acct-head">
        <strong>{record.acc}</strong>
        <span className={`badge badge-${live.status}`}>{live.status}</span>
      </p>
      <p className="muted">
        {formatSize(record.size_cents)} {record.product_id} · {STAGE_NAMES[live.rules.stage] ?? live.rules.stage}
      </p>
      <p className="acct-balance">{formatCents(live.equity_cents)}</p>
      <div className="acct-rings">
        {target !== null && (
          <figure>
            <Ring value={progress} label={`${record.acc}: profit target reached`} tone="ok" size={72}>
              {Math.round(Math.min(1, Math.max(0, progress)) * 100)}%
            </Ring>
            <figcaption>Target</figcaption>
          </figure>
        )}
        <figure>
          <Ring value={room} label={`${record.acc}: maximum loss room left`} tone={roomTone} size={72}>
            {Math.round(Math.min(1, Math.max(0, room)) * 100)}%
          </Ring>
          <figcaption>Loss room</figcaption>
        </figure>
        <figure>
          <Ring value={live.rules.min_trading_days > 0 ? live.trading_days / live.rules.min_trading_days : 1} label={`${record.acc}: trading days`} tone="neutral" size={72}>
            {live.trading_days}/{live.rules.min_trading_days}
          </Ring>
          <figcaption>Days</figcaption>
        </figure>
      </div>
      <Link className="button button-secondary" href={`/accounts/${record.acc}`}>
        Open {record.acc}
      </Link>
    </li>
  );
}

function UpcomingNews() {
  const api = useApi();
  const [events, setEvents] = useState<NewsEvent[] | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    api.get<NewsEvent[]>("/calendar").then(setEvents, () => {
      setEvents([]);
    });
    const t = window.setInterval(() => {
      setNow(Date.now());
    }, 30_000);
    return () => {
      window.clearInterval(t);
    };
  }, [api]);
  const next = (events ?? []).filter((e) => e.end_ts > now).slice(0, 3);
  return (
    <Card title="Restricted news next" actions={<Link href="/news">Full calendar</Link>}>
      {events === null ? (
        <Loading />
      ) : next.length === 0 ? (
        <p className="muted">Nothing scheduled.</p>
      ) : (
        <ul className="mini-news">
          {next.map((e) => (
            <li key={e.event_id} className={e.restricted === false ? undefined : "restricted"}>
              <ImpactMark impact={e.impact} />
              <span>
                <strong>{e.title ?? e.event_id}</strong>
                <span className="muted">
                  in {countdown(e.start_ts - now)} · {e.symbols.join(", ")}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">Binds funded accounts only.</p>
    </Card>
  );
}

/** The signed-in home: the next thing to do, each account at a glance, the
next restricted releases. */
function Home({ me, mfa }: { me: Me; mfa: boolean }) {
  const api = useApi();
  const [live, setLive] = useState<Record<string, LiveAccount | undefined>>({});
  const accounts = [...me.accounts].sort((a, b) => b.created_at - a.created_at).slice(0, 6);
  const ids = accounts.map((a) => a.acc).join(",");
  useEffect(() => {
    let on = true;
    for (const acc of ids.split(",").filter(Boolean)) {
      api.get<LiveAccount>(`/accounts/${acc}`).then(
        (a) => {
          if (on) setLive((m) => ({ ...m, [acc]: a }));
        },
        () => undefined,
      );
    }
    return () => {
      on = false;
    };
  }, [api, ids]);
  const step = nextStep(me, live);
  const first = me.full_name.split(" ")[0] ?? me.full_name;
  return (
    <div className="page home">
      <h1>Hello, {first}</h1>
      <section className="next-step" aria-label="Next step">
        <p>{step.text}</p>
        <Link className="button button-primary" href={step.href}>
          {step.action}
        </Link>
      </section>
      {!mfa && (
        <Notice tone="info">
          Two-factor authentication is off for this sign-in. <Link href="/security">Turn it on</Link> before you request a payout.
        </Notice>
      )}
      <div className="home-grid">
        <section aria-labelledby="accounts-title">
          <div className="section-head">
            <h2 id="accounts-title">Your accounts</h2>
            <Link href="/accounts">All accounts</Link>
          </div>
          {accounts.length === 0 ? (
            <Card>
              <p>No accounts yet.</p>
            </Card>
          ) : (
            <ul className="acct-list">
              {accounts.map((a) => (
                <AccountTile key={a.acc} record={a} live={live[a.acc]} />
              ))}
            </ul>
          )}
        </section>
        <UpcomingNews />
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

/** A challenge card that tilts under the pointer, with a sheen that follows it. */
function Tile({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLLIElement>(null);
  useTilt(ref, 7);
  return (
    <li ref={ref} className="tile" data-reveal="">
      {children}
    </li>
  );
}

/** One home page section as the firm's editors approved it (rule AC07); its
figures already filled from the products in force (AC08). */
export interface SiteSection {
  fields: Record<string, string>;
  items: Record<string, string>[];
}
export type Site = Partial<Record<"hero" | "challenges" | "how" | "stats", SiteSection>>;

/** The signed-out home page: what a challenge is, what it costs, how it works. */
function Landing({ onSignIn, onSignUp, registering }: { onSignIn: () => void; onSignUp: () => void; registering: boolean }) {
  const api = useApi();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [site, setSite] = useState<Site>({});
  useEffect(() => {
    api.get<Product[]>("/catalogue").then(setProducts, () => {
      setProducts([]);
    });
    api.get<Site>("/site").then(setSite, () => {
      setSite({}); // the page's own words
    });
  }, [api]);
  const hero = site.hero?.fields;
  const how = site.how;
  const steps = how !== undefined && how.items.length > 0 ? how.items.map((i) => ({ title: i.title ?? "", text: i.text ?? "" })) : STEPS;
  const first = products?.[0]?.stages[0];
  const page = useRef<HTMLDivElement>(null);
  const heroArea = useRef<HTMLElement>(null);
  useReveal(page, [products]);
  const [webgl, setWebgl] = useState(true);
  const noWebgl = useCallback(() => {
    setWebgl(false);
  }, []);
  const split = products?.reduce((best, p) => Math.max(best, Number(p.profit_split_percent)), 0);
  return (
    <div className="landing" ref={page}>
      <section className="hero" aria-labelledby="hero-title" ref={heroArea}>
        {webgl ? <Terrain area={heroArea} onUnavailable={noWebgl} /> : null}
        <h1 id="hero-title">
          {hero?.title_line_1 ?? "Trade the evaluation."}
          <br />
          {hero?.title_line_2 ?? `Keep ${split === undefined || split === 0 ? "most" : `${split.toString()} %`} of the profit.`}
        </h1>
        <p className="lead">
          {hero?.lead ?? "Prove your trading on a simulated account. Pass, and trade a funded account with a share of every profit."}
        </p>
        <div className="row">
          {registering ? (
            <button type="button" className="button button-primary button-large" onClick={onSignUp}>
              Get started
            </button>
          ) : null}
          <Link className={`button button-large ${registering ? "button-secondary" : "button-primary"}`} href="/shop">
            {hero?.button ?? "Choose a challenge"}
          </Link>
        </div>
        <p className="hero-signin muted">
          Already a trader?{" "}
          <button type="button" className="link-button" onClick={onSignIn}>
            Sign in
          </button>
        </p>
        <Ridge
          target={first?.profit_target_percent === null || first === undefined ? undefined : formatPercent(first.profit_target_percent)}
          maxLoss={first === undefined ? undefined : formatPercent(first.max_loss_percent)}
          area={heroArea}
          layers={!webgl}
        />
      </section>

      {site.stats !== undefined && site.stats.items.length > 0 ? (
        <section className="stats" aria-labelledby="stats-title" data-reveal="">
          <h2 id="stats-title" className="visually-hidden">
            {site.stats.fields.heading ?? "In numbers"}
          </h2>
          <ul>
            {site.stats.items.map((i) => (
              <li key={`${i.label ?? ""}${i.value ?? ""}`}>
                <strong>{i.value}</strong>
                <span className="muted">{i.label}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {products !== null && products.length > 0 && (
        <section className="tiles" id="challenges" aria-labelledby="tiles-title">
          <h2 id="tiles-title" data-reveal="">
            {site.challenges?.fields.heading ?? "Challenges"}
          </h2>
          <ul className="tile-list" aria-label="Challenges: swipe for more on a phone">
            {products.map((p) => {
              const from = cheapest(p);
              const evaluations = p.stages.filter((st) => st.stage !== "funded").length;
              return (
                <Tile key={p.id}>
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
                </Tile>
              );
            })}
          </ul>
        </section>
      )}

      <section className="how" id="how" aria-labelledby="how-title">
        <h2 id="how-title" data-reveal="">
          {how?.fields.heading ?? "How it works"}
        </h2>
        <ol className="how-steps">
          {steps.map((s) => (
            <li key={s.title} data-reveal="">
              <h3>{s.title}</h3>
              <p className="muted">{s.text}</p>
            </li>
          ))}
        </ol>
        <p className="muted small">{how?.fields.footnote ?? "All trading here is simulated. Each challenge's rules are shown before you buy."}</p>
      </section>

      <section className="faq" id="faq" aria-labelledby="faq-title">
        <h2 id="faq-title" data-reveal="">
          Questions traders ask
        </h2>
        <div className="faq-list">
          {FAQ.map((q) => (
            <details key={q.q} data-reveal="">
              <summary>{q.q}</summary>
              <p className="muted">{q.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="closing" aria-labelledby="closing-title" data-reveal="">
        <h2 id="closing-title">Start your challenge today</h2>
        <p className="muted">Pick an evaluation, trade it on a simulated account, and earn a share of the profit on a funded account.</p>
        <div className="row">
          {registering ? (
            <button type="button" className="button button-primary button-large" onClick={onSignUp}>
              Get started
            </button>
          ) : null}
          <Link className="button button-secondary button-large" href="/shop">
            See the challenges
          </Link>
        </div>
      </section>
    </div>
  );
}

/** Plain answers that hold for every product; figures live on each
challenge's own rules (rule AC08: no typed figures). */
const FAQ: { q: string; a: string }[] = [
  {
    q: "Is the trading real?",
    a: "No. Every account here, in the evaluation and when funded, is simulated. You trade real market prices without risking your own money beyond the challenge fee.",
  },
  {
    q: "How do I join?",
    a: "Press Get started, create your account with your email and a password, and add an authenticator app. Then choose a challenge and pay for it.",
  },
  {
    q: "What are the rules?",
    a: "Each challenge shows its profit target, daily loss limit and maximum loss before you buy, and your account page shows them while you trade. The trading day resets at 17:00 New York time.",
  },
  {
    q: "What happens when I pass?",
    a: "We check your identity with our provider (a photo ID and a selfie), review the account, and you sign the funded agreement. Then your funded account opens.",
  },
  {
    q: "How am I paid?",
    a: "From a funded account you request a payout of your share of the profit. It is sent to your USDT wallet on the TRON network (TRC-20).",
  },
  {
    q: "Can I trade during news?",
    a: "On funded accounts, trading around the restricted high-impact releases shown in the economic calendar is not allowed. Evaluations are not restricted.",
  },
];
