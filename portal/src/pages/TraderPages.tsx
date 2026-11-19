import { useEffect, useMemo, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { LeaderboardRow, Me, Order, Payout, Referrals } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatCents, formatDate, formatSize } from "../lib/money";
import { Link } from "../router";

/** Pages a trader keeps, as FTMO's client area: profile, billing with
printable invoices, certificates for each stage passed and each payout,
and the public leaderboard. */

const PRODUCTS: Record<string, string> = { lite: "Lite", classic: "Classic", "one-step": "1-Step", instant: "Instant" };
const STAGES: Record<string, string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };

export function productName(id: string): string {
  return PRODUCTS[id] ?? id;
}

export function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** Basis points (hundredths of a percent) as signed text: 812 -> "+8.12%". */
export function gainText(bp: number): string {
  const sign = bp > 0 ? "+" : bp < 0 ? "-" : "";
  const abs = Math.abs(bp);
  return `${sign}${Math.floor(abs / 100).toString()}.${(abs % 100).toString().padStart(2, "0")}%`;
}

/** Signed in and registered, or the page's own message. */
function Gate({ title, back, me, children }: { title: string; back: string; me: ReturnType<typeof useMe>; children: (me: Me) => React.ReactNode }) {
  const auth = useAuth();
  if (me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page">
        <h1>{title}</h1>
        <Button onClick={() => void auth.signIn(back)}>Sign in</Button>
      </div>
    );
  }
  if (me.status === "unregistered") {
    return (
      <div className="page">
        <h1>{title}</h1>
        <Notice>
          Create your trader profile first on the <Link href="/">overview</Link>.
        </Notice>
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  return <>{children(me.me)}</>;
}

// --- profile --------------------------------------------------------------------

const IDENTITY: Record<string, string> = {
  verification: "Waiting for your identity check",
  verification_declined: "Identity check declined",
  checks: "Being checked",
  review: "In review",
  rejected: "Not approved",
  contract: "Verified",
  signature: "Verified",
  signed: "Verified",
  provisioned: "Verified",
  failed: "Verified",
};

/** The identity check state from the latest funded onboarding. */
export function identityState(me: Pick<Me, "onboardings">): string {
  const latest = [...me.onboardings].sort((a, b) => b.onb.localeCompare(a.onb))[0];
  return latest === undefined ? "Not needed yet: it comes with your first funded account" : (IDENTITY[latest.status] ?? latest.status);
}

function LeaderboardSwitch() {
  const api = useApi();
  const [visible, setVisible] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.get<{ visible: boolean }>("/me/leaderboard").then(
      (r) => {
        if (live) setVisible(r.visible);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  if (visible === null) return null;
  const change = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.put<{ visible: boolean }>("/me/leaderboard", { visible: !visible });
      setVisible(r.visible);
    } catch {
      setError("The change was not saved. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      title="Leaderboard"
      actions={
        <Button variant="secondary" disabled={busy} onClick={() => void change()}>
          {visible ? "Hide me" : "Show me"}
        </Button>
      }
    >
      <p>
        {visible
          ? "Your funded accounts can appear on the public leaderboard, as your first name, the initial of your last name and your country."
          : "You are hidden from the public leaderboard."}
      </p>
      {error === null ? null : <Notice tone="error">{error}</Notice>}
    </Card>
  );
}

/** The trader's referral link and what it has earned (D-14.15). */
export function ReferralsCard() {
  const api = useApi();
  const [r, setR] = useState<Referrals | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    api.get<Referrals>("/me/referrals").then(setR, () => {
      setR(null); // not shown when it cannot be read
    });
  }, [api]);
  if (r === null || !r.on || r.code === null) return null;
  const link = `${window.location.origin}/?ref=${r.code}`;
  return (
    <Card title="Refer a trader">
      <p>
        Share your link. When a new trader signs up with it, you earn {r.commission_percent} % of what they pay for their first
        account, available {r.holdback_days} days after they pay.
      </p>
      <p className="referral-link">
        <code>{link}</code>{" "}
        <Button
          variant="secondary"
          onClick={() => {
            void navigator.clipboard.writeText(link).then(
              () => {
                setCopied(true);
              },
              () => {
                setCopied(false);
              },
            );
          }}
        >
          {copied ? "Copied" : "Copy link"}
        </Button>
      </p>
      <dl className="facts">
        <dt>Traders referred</dt>
        <dd>{r.referred}</dd>
        <dt>Earned</dt>
        <dd>{formatCents(r.accrued_cents)}</dd>
        <dt>Available</dt>
        <dd>{formatCents(r.available_cents)}</dd>
      </dl>
    </Card>
  );
}

export function ProfilePage() {
  const me = useMe();
  return (
    <Gate title="Profile" back="/profile" me={me}>
      {(m) => (
        <div className="page narrow">
          <h1>Profile</h1>
          <Card title="Personal details">
            <dl className="facts">
              <dt>Name</dt>
              <dd>{m.full_name}</dd>
              <dt>Email</dt>
              <dd>{m.email}</dd>
              <dt>Date of birth</dt>
              <dd>{m.date_of_birth}</dd>
              <dt>Country of residence</dt>
              <dd>{countryName(m.residence_country)}</dd>
              <dt>Client number</dt>
              <dd>{m.cif}</dd>
              <dt>Member since</dt>
              <dd>{formatDate(m.created_at)}</dd>
              <dt>Identity check</dt>
              <dd>{identityState(m)}</dd>
            </dl>
            <p className="muted">
              Your name, date of birth and country must match your identity documents, so they are not changed here. To
              correct one, write to support.
            </p>
          </Card>
          <Card title="Sign-in and security">
            <p>Password, email for sign-in and the authenticator app are managed on the security page.</p>
            <Link className="button button-secondary" href="/security">
              Security
            </Link>
          </Card>
          <LeaderboardSwitch />
          <ReferralsCard />
          <Card title="Billing and certificates">
            <p>
              <Link href="/billing">Billing</Link>: your orders and invoices. <Link href="/certificates">Certificates</Link>: one
              for each stage you pass and each payout.
            </p>
          </Card>
        </div>
      )}
    </Gate>
  );
}

// --- billing --------------------------------------------------------------------

const ORDER_STATUS: Record<string, string> = { pending: "Awaiting payment", paid: "Paid", provisioned: "Paid", failed: "Failed" };

export function isInvoiced(o: Pick<Order, "paid_at">): boolean {
  return o.paid_at !== null;
}

export function BillingPage() {
  const me = useMe();
  return (
    <Gate title="Billing" back="/billing" me={me}>
      {(m) => {
        const paid = m.orders.filter(isInvoiced);
        const total = paid.reduce((sum, o) => sum + o.price_cents, 0);
        return (
          <div className="page">
            <h1>Billing</h1>
            <p className="muted">
              {paid.length.toString()} paid {paid.length === 1 ? "order" : "orders"}, {formatCents(total)} in all. Open an
              invoice to print it or save it as a PDF.
            </p>
            {m.orders.length === 0 ? (
              <Card>
                <p>No orders yet.</p>
                <Link className="button button-primary" href="/shop">
                  Choose a challenge
                </Link>
              </Card>
            ) : (
              <div className="table-wrap">
                <table>
                  <caption className="visually-hidden">Your orders</caption>
                  <thead>
                    <tr>
                      <th scope="col">Order</th>
                      <th scope="col">Date</th>
                      <th scope="col">Item</th>
                      <th scope="col">Price</th>
                      <th scope="col">Status</th>
                      <th scope="col">Invoice</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.orders.map((o) => (
                      <tr key={o.ord}>
                        <th scope="row">{o.ord}</th>
                        <td>{formatDate(o.created_at)}</td>
                        <td>
                          {productName(o.product_id)} {formatSize(o.size_cents)}
                        </td>
                        <td>{formatCents(o.price_cents)}</td>
                        <td>{ORDER_STATUS[o.status] ?? o.status}</td>
                        <td>{isInvoiced(o) ? <Link href={`/billing/${o.ord}`}>Invoice</Link> : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      }}
    </Gate>
  );
}

/** The seller's details as set under General in the back office. */
function useContact(): Record<string, string> {
  const api = useApi();
  const [contact, setContact] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    api.get<Record<string, { fields: Record<string, string> } | undefined>>("/site").then(
      (s) => {
        if (live) setContact(s.contact?.fields ?? {});
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  return contact;
}

export function InvoicePage({ ord }: { ord: string }) {
  const me = useMe();
  const contact = useContact();
  return (
    <Gate title="Invoice" back={`/billing/${ord}`} me={me}>
      {(m) => {
        const o = m.orders.find((x) => x.ord === ord);
        if (o === undefined || !isInvoiced(o)) return <Notice tone="error">No invoice for this order.</Notice>;
        const discount = o.list_price_cents - o.price_cents;
        return (
          <div className="page narrow printable">
            <div className="print-actions">
              <Link href="/billing">Back to billing</Link>
              <Button variant="secondary" onClick={() => { window.print(); }}>
                Print or save as PDF
              </Button>
            </div>
            <article className="invoice" aria-labelledby="invoice-title">
              <header>
                <h1 id="invoice-title">Invoice {o.ord}</h1>
                <p>Paid {formatDate(o.paid_at ?? o.created_at)}</p>
              </header>
              <div className="invoice-parties">
                <div>
                  <h2>From</h2>
                  <p>{contact.company_name ?? "pipridge"}</p>
                  {contact.address ? <p>{contact.address}</p> : null}
                  {contact.support_email ? <p>{contact.support_email}</p> : null}
                </div>
                <div>
                  <h2>To</h2>
                  <p>{m.full_name}</p>
                  <p>{m.email}</p>
                  <p>{countryName(m.residence_country)}</p>
                  <p>Client {m.cif}</p>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Item</th>
                    <th scope="col">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      {productName(o.product_id)} challenge, {formatSize(o.size_cents)} account
                    </td>
                    <td>{formatCents(o.list_price_cents)}</td>
                  </tr>
                  {discount > 0 ? (
                    <tr>
                      <td>Discount{o.discount_code ? ` (code ${o.discount_code})` : ""}</td>
                      <td>-{formatCents(discount)}</td>
                    </tr>
                  ) : null}
                </tbody>
                <tfoot>
                  <tr>
                    <th scope="row">Total paid (USD)</th>
                    <td>{formatCents(o.price_cents)}</td>
                  </tr>
                </tfoot>
              </table>
              <p className="muted">A fee for a trading evaluation on simulated accounts. Not a deposit; no investment.</p>
            </article>
          </div>
        );
      }}
    </Gate>
  );
}

// --- certificates -----------------------------------------------------------------

export interface Certificate {
  id: string; // PRG… or PO…
  kind: "pass" | "payout";
  title: string;
  acc: string;
  detail: string;
  at: number;
}

/** A certificate for each stage passed and each payout paid, newest first. */
export function certificates(me: Pick<Me, "progressions">, payouts: readonly Payout[]): Certificate[] {
  const passes: Certificate[] = me.progressions.map((p) => ({
    id: p.prg,
    kind: "pass",
    title: `${STAGES[p.from_stage] ?? p.from_stage} passed`,
    acc: p.from_acc,
    detail: `${productName(p.product_id)} ${formatSize(p.size_cents)}`,
    at: p.passed_at,
  }));
  const paid: Certificate[] = payouts
    .filter((p) => p.status === "paid" && p.paid_at !== null)
    .map((p) => ({ id: p.po, kind: "payout", title: "Payout", acc: p.acc, detail: formatCents(p.trader_amount_cents), at: p.paid_at ?? 0 }));
  return [...passes, ...paid].sort((a, b) => b.at - a.at || a.id.localeCompare(b.id));
}

function usePayouts(me: Me | null): Payout[] | null {
  const api = useApi();
  const [payouts, setPayouts] = useState<Payout[] | null>(null);
  const funded = useMemo(() => (me === null ? [] : me.accounts.filter((a) => a.stage === "funded").map((a) => a.acc)), [me]);
  useEffect(() => {
    if (me === null) return;
    let live = true;
    Promise.all(funded.map((acc) => api.get<Payout[]>(`/accounts/${acc}/payouts`).catch(() => [] as Payout[]))).then(
      (lists) => {
        if (live) setPayouts(lists.flat());
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api, me, funded]);
  return payouts;
}

function CertificateList({ me }: { me: Me }) {
  const payouts = usePayouts(me);
  if (payouts === null) return <Loading />;
  const list = certificates(me, payouts);
  return (
    <div className="page">
      <h1>Certificates</h1>
      {list.length === 0 ? (
        <Card>
          <p>Pass a stage or receive a payout and its certificate appears here.</p>
        </Card>
      ) : (
        <div className="cert-grid">
          {list.map((c) => (
            <Link key={c.id} className="cert-tile" href={`/certificates/${c.id}`}>
              <span className="cert-kind">{c.kind === "pass" ? "Stage passed" : "Payout"}</span>
              <strong>{c.title}</strong>
              <span>
                {c.detail} · {c.acc}
              </span>
              <span className="muted">{formatDate(c.at)}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function CertificatesPage() {
  const me = useMe();
  return (
    <Gate title="Certificates" back="/certificates" me={me}>
      {(m) => <CertificateList me={m} />}
    </Gate>
  );
}

function CertificateView({ me, id }: { me: Me; id: string }) {
  const payouts = usePayouts(me);
  if (payouts === null) return <Loading />;
  const c = certificates(me, payouts).find((x) => x.id === id);
  if (c === undefined) return <Notice tone="error">No such certificate.</Notice>;
  return (
    <div className="page printable">
      <div className="print-actions">
        <Link href="/certificates">All certificates</Link>
        <Button variant="secondary" onClick={() => { window.print(); }}>
          Print or save as PDF
        </Button>
      </div>
      <article className="certificate" aria-labelledby="cert-title">
        <img src="/brand/logo.svg" alt="" className="certificate-logo" />
        <p className="certificate-kicker">{c.kind === "pass" ? "Certificate of achievement" : "Certificate of payout"}</p>
        <h1 id="cert-title">{me.full_name}</h1>
        <p className="certificate-line">
          {c.kind === "pass" ? (
            <>
              passed <strong>{c.title.replace(" passed", "")}</strong> of the {c.detail} evaluation
            </>
          ) : (
            <>
              received a payout of <strong>{c.detail}</strong>
            </>
          )}
        </p>
        <p className="certificate-meta">
          Account {c.acc} · {formatDate(c.at)} · Reference {c.id}
        </p>
      </article>
    </div>
  );
}

export function CertificatePage({ id }: { id: string }) {
  const me = useMe();
  return (
    <Gate title="Certificate" back={`/certificates/${id}`} me={me}>
      {(m) => <CertificateView me={m} id={id} />}
    </Gate>
  );
}

// --- leaderboard ------------------------------------------------------------------

export function LeaderboardPage() {
  const api = useApi();
  const [rows, setRows] = useState<LeaderboardRow[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let live = true;
    api.get<{ rows: LeaderboardRow[] }>("/leaderboard").then(
      (r) => {
        if (live) setRows(r.rows);
      },
      () => {
        if (live) setError(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api]);
  return (
    <div className="page">
      <h1>Leaderboard</h1>
      <p className="muted">
        Funded traders ranked by their gain on the starting balance, updated every minute. Names show the first name and an
        initial; traders can hide themselves in their profile.
      </p>
      {error ? <Notice tone="error">The leaderboard could not be loaded.</Notice> : null}
      {rows === null && !error ? <Loading /> : null}
      {rows !== null && rows.length === 0 ? (
        <Card>
          <p>No funded traders on the board yet.</p>
        </Card>
      ) : null}
      {rows !== null && rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="visually-hidden">Leaderboard</caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Trader</th>
                <th scope="col">Country</th>
                <th scope="col">Account</th>
                <th scope="col">Gain</th>
                <th scope="col">Profit</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.rank}>
                  <td>{r.rank}</td>
                  <th scope="row">{r.name}</th>
                  <td>{countryName(r.country)}</td>
                  <td>
                    {productName(r.product_id)} {formatSize(r.size_cents)}
                  </td>
                  <td className={r.gain_basis_points >= 0 ? "gain-up" : "gain-down"}>{gainText(r.gain_basis_points)}</td>
                  <td>{formatCents(r.gain_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
