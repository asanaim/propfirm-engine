import { useEffect, useState, type SyntheticEvent } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import { payoutDelayText, useSiteStatus } from "../components/SiteStatus";
import type { PayMethod } from "./ShopPage";
import { ApiError } from "../api/client";
import type { AccountRecord, Eligibility, Payout, PayoutStatus, Wallet } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatCents, formatDate, formatSize } from "../lib/money";
import { Link } from "../router";

/** Payouts (screens 4d): the payout wallet (USDT, TRC-20) and, for each
funded account, what is payable now, the request, and the payouts made. */

export const PAYOUT_TEXT: Record<PayoutStatus, string> = {
  requested: "Being approved",
  approved: "Approved",
  debited: "Taken from the account",
  sent: "Sent",
  paid: "Paid",
  denied: "Denied",
  cancelled: "Cancelled",
  failed: "Failed",
};

const CHECK_TEXT: Record<string, string> = {
  pass: "Met",
  fail: "Not met",
  not_decided_here: "Checked on request",
};

/** A TRON address shape (the API checks its Base58Check checksum). */
export function looksLikeTron(address: string): boolean {
  return /^T[1-9A-HJ-NP-Za-km-z]{33}$/.test(address);
}

export function PayoutsPage() {
  const auth = useAuth();
  const me = useMe();
  const site = useSiteStatus();
  const [round, setRound] = useState(0);
  if (me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page">
        <h1>Payouts</h1>
        <Button onClick={() => void auth.signIn("/payouts")}>Sign in to see your payouts</Button>
      </div>
    );
  }
  if (me.status === "unregistered") {
    return (
      <div className="page">
        <h1>Payouts</h1>
        <Notice>
          Create your trader profile first on the <Link href="/">overview</Link>.
        </Notice>
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const funded = me.me.accounts.filter((a) => a.stage === "funded");
  const walletSaved = () => {
    setRound((r) => r + 1);
  };
  return (
    <div className="page">
      <h1>Payouts</h1>
      {site.status.payouts_delayed ? <Notice>{payoutDelayText(site.status.payouts_delayed_since)}</Notice> : null}
      <WalletCard onSaved={walletSaved} />
      {funded.length === 0 ? (
        <Card>
          <p>
            Payouts are made from funded accounts. See <Link href="/funded">Funded</Link> for your onboarding.
          </p>
        </Card>
      ) : (
        funded.map((a) => <AccountPayouts key={`${a.acc}-${String(round)}`} account={a} />)
      )}
    </div>
  );
}

function WalletCard({ onSaved }: { onSaved: () => void }) {
  const api = useApi();
  const [wallet, setWallet] = useState<Wallet | null | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [address, setAddress] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api.get<Wallet>("/me/wallet").then(
      (found) => {
        if (alive) setWallet(found);
      },
      (e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) setWallet(null);
        else setError(e);
      },
    );
    return () => {
      alive = false;
    };
  }, [api]);
  const trimmed = address.trim();
  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setWallet(await api.post<Wallet>("/me/wallet", { address: trimmed }));
      onSaved(); // the payout checks include the wallet
      setEditing(false);
      setAddress("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  if (wallet === undefined) return error !== null ? <Problem error={error} /> : <Loading label="Loading your wallet" />;
  const form = (
    <form className="form" onSubmit={(e) => void submit(e)}>
      <label className="field">
        <span>USDT wallet address (TRON, TRC-20)</span>
        <input
          type="text"
          value={address}
          spellCheck={false}
          autoComplete="off"
          maxLength={34}
          onChange={(e) => {
            setAddress(e.target.value);
          }}
          aria-invalid={trimmed !== "" && !looksLikeTron(trimmed)}
        />
      </label>
      {trimmed !== "" && !looksLikeTron(trimmed) && (
        <p className="muted">A TRON address starts with T and has 34 characters.</p>
      )}
      <p className="muted">
        Changing the wallet needs your authenticator code. We email you every change; payouts go only to this wallet.
      </p>
      {error !== null && <Problem error={error} />}
      <Button type="submit" busy={busy} disabled={!looksLikeTron(trimmed)}>
        Save wallet
      </Button>
    </form>
  );
  return (
    <Card title="Payout wallet">
      {wallet === null ? (
        <>
          <p>Add the wallet your payouts are sent to.</p>
          {form}
        </>
      ) : (
        <>
          <dl className="facts">
            <dt>Address</dt>
            <dd>
              <code>{wallet.address}</code>
            </dd>
            <dt>Network</dt>
            <dd>USDT on TRON (TRC-20)</dd>
            <dt>Added</dt>
            <dd>{formatDate(wallet.added_at)}</dd>
          </dl>
          {editing ? (
            form
          ) : (
            <Button
              variant="secondary"
              onClick={() => {
                setEditing(true);
              }}
            >
              Change wallet
            </Button>
          )}
        </>
      )}
    </Card>
  );
}

/** A withdrawal method's charge on an amount, as the server takes it: the
fixed charge plus the percent, rounded up to a whole cent (D-14.25). */
export function methodCharge(amountCents: number, fixedCents: number, percent: string): number {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(percent.trim());
  if (m === null) return fixedCents;
  const decimals = m[2] ?? "";
  const scale = 10n ** BigInt(decimals.length);
  const numerator = BigInt(m[1] ?? "0") * scale + BigInt(decimals === "" ? "0" : decimals);
  const share = BigInt(amountCents) * numerator;
  const denominator = 100n * scale;
  return fixedCents + Number((share + denominator - 1n) / denominator);
}

/** Whether a method takes this amount (a maximum of 0: none). */
export function methodAllows(m: PayMethod, amountCents: number): boolean {
  return amountCents >= m.min_cents && (m.max_cents === 0 || amountCents <= m.max_cents);
}

function AccountPayouts({ account }: { account: AccountRecord }) {
  const api = useApi();
  const [methods, setMethods] = useState<PayMethod[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [payouts, setPayouts] = useState<Payout[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    Promise.all([
      api.get<Eligibility>(`/accounts/${account.acc}/payout`),
      api.get<Payout[]>(`/accounts/${account.acc}/payouts`),
      api.get<PayMethod[]>("/payment-methods?kind=withdrawal").catch(() => []),
    ]).then(
      ([e, p, m]) => {
        if (!alive) return;
        setEligibility(e);
        setPayouts(p);
        setMethods(m);
      },
      (e: unknown) => {
        if (alive) setError(e);
      },
    );
    return () => {
      alive = false;
    };
  }, [api, account.acc, round]);
  const act = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await call();
      setRound((r) => r + 1);
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(false);
    }
  };
  const title = (
    <>
      <Link href={`/accounts/${account.acc}`}>{account.acc}</Link> · {formatSize(account.size_cents)}{" "}
      {account.product_id}
    </>
  );
  if (eligibility === null || payouts === null) {
    return <Card title={title}>{error !== null ? <Problem error={error} /> : <Loading />}</Card>;
  }
  const pending = payouts.some((p) => p.status === "requested" || p.status === "approved");
  const amount = eligibility.trader_amount_cents;
  const method = methods.find((m) => m.method_id === chosen) ?? methods[0] ?? null;
  const charge = method === null ? 0 : methodCharge(amount, method.fixed_charge_cents, method.percent_charge);
  const fits = method === null || (methodAllows(method, amount) && charge < amount);
  return (
    <Card title={title}>
      <dl className="facts">
        <dt>Payable profit</dt>
        <dd>{formatCents(eligibility.payable_cents)}</dd>
        <dt>Your share ({eligibility.split_percent} %)</dt>
        <dd>{formatCents(eligibility.trader_share_cents)}</dd>
        {eligibility.fee_refund_cents > 0 && (
          <>
            <dt>Fee refund</dt>
            <dd>{formatCents(eligibility.fee_refund_cents)}</dd>
          </>
        )}
        {method !== null && charge > 0 && (
          <>
            <dt>{method.name} charge</dt>
            <dd>{formatCents(charge)}</dd>
          </>
        )}
        <dt>You receive</dt>
        <dd>
          <strong>{formatCents(amount - charge)}</strong>
        </dd>
        {eligibility.opens_at !== null && !eligibility.eligible && (
          <>
            <dt>Next payout window</dt>
            <dd>{formatDate(eligibility.opens_at)}</dd>
          </>
        )}
      </dl>
      <ul className="checks" aria-label="Payout checks">
        {eligibility.checks.map((c) => (
          <li key={c.name} className={`check-${c.status}`}>
            <strong>{CHECK_TEXT[c.status] ?? c.status}:</strong> {c.detail}
          </li>
        ))}
      </ul>
      {actionError !== null && <Problem error={actionError} />}
      {eligibility.eligible && !pending && methods.length > 1 && (
        <fieldset className="segmented" aria-label="Withdrawal method">
          {methods.map((m) => (
            <label key={m.method_id} className="segment">
              <input
                type="radio"
                name={`method-${account.acc}`}
                checked={method?.method_id === m.method_id}
                onChange={() => {
                  setChosen(m.method_id);
                }}
              />
              <span>{m.name}</span>
            </label>
          ))}
        </fieldset>
      )}
      {eligibility.eligible && !pending && !fits && (
        <Notice>
          {method.name} takes payouts from {formatCents(method.min_cents)}
          {method.max_cents > 0 ? ` to ${formatCents(method.max_cents)}` : ""}.
        </Notice>
      )}
      {eligibility.eligible && !pending && (
        <Button
          busy={busy}
          disabled={!fits}
          onClick={() =>
            void act(() =>
              api.post<Payout>(`/accounts/${account.acc}/payouts`, method === null ? undefined : { method: method.method_id }),
            )
          }
        >
          Request payout of {formatCents(amount - charge)}
        </Button>
      )}
      {pending && <Notice>A payout of this account is in progress. New orders wait until it is taken out.</Notice>}
      {payouts.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption>Payouts of {account.acc}</caption>
            <thead>
              <tr>
                <th scope="col">Payout</th>
                <th scope="col">Amount</th>
                <th scope="col">Status</th>
                <th scope="col">Requested</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {payouts.map((p) => (
                <tr key={p.po}>
                  <th scope="row">{p.po}</th>
                  <td>{formatCents(p.net_cents ?? p.trader_amount_cents)}</td>
                  <td>
                    {PAYOUT_TEXT[p.status]}
                    {p.failure !== null && ` (${p.failure})`}
                  </td>
                  <td>{formatDate(p.requested_at)}</td>
                  <td>
                    {p.status === "requested" && (
                      <Button
                        variant="quiet"
                        busy={busy}
                        onClick={() => void act(() => api.post<Payout>(`/me/payouts/${p.po}/cancel`))}
                      >
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
