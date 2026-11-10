import { useEffect, useState, type SyntheticEvent } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { Contract, Onboarding, OnboardingStatus } from "../api/types";
import { useSection } from "../components/SiteStatus";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatDate, formatSize } from "../lib/money";
import { Link } from "../router";

/** Funded onboarding (step 3b of the plan, screens 4d): the identity check,
the firm's review, the funded agreement and its signature. */

const STEPS: { key: string; label: string; statuses: OnboardingStatus[] }[] = [
  { key: "verify", label: "Verify your identity", statuses: ["verification", "verification_declined"] },
  { key: "review", label: "Checks and review", statuses: ["checks", "review"] },
  { key: "sign", label: "Sign the agreement", statuses: ["contract", "signature"] },
  { key: "open", label: "Funded account opens", statuses: ["signed", "provisioned"] },
];

export const STATUS_TEXT: Record<OnboardingStatus, string> = {
  verification: "Verify your identity",
  verification_declined: "Identity check declined: try again",
  checks: "Being checked",
  review: "Being reviewed",
  rejected: "Not approved",
  contract: "Agreement being prepared",
  signature: "Ready to sign",
  signed: "Account being opened",
  provisioned: "Funded account open",
  failed: "Could not be completed",
};

function stepIndex(status: OnboardingStatus): number {
  return STEPS.findIndex((s) => s.statuses.includes(status));
}

/** SHA-256 of the text in hex, as the API computes it (UTF-8). */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function FundedPage() {
  const auth = useAuth();
  const me = useMe();
  if (me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page">
        <h1>Funded</h1>
        <Button onClick={() => void auth.signIn("/funded")}>Sign in to see your funded onboarding</Button>
      </div>
    );
  }
  if (me.status === "unregistered") {
    return (
      <div className="page">
        <h1>Funded</h1>
        <Notice>
          Create your trader profile first on the <Link href="/">overview</Link>.
        </Notice>
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const onboardings = me.me.onboardings;
  return (
    <div className="page">
      <h1>Funded</h1>
      {onboardings.length === 0 ? (
        <Card>
          <p>
            When you pass your evaluation (or buy an instant funded account), the steps to open your funded account
            appear here: identity check, review, and the funded agreement.
          </p>
        </Card>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="visually-hidden">Your funded onboardings</caption>
            <thead>
              <tr>
                <th scope="col">Onboarding</th>
                <th scope="col">Account</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {onboardings.map((o) => (
                <tr key={o.onb}>
                  <th scope="row">
                    <Link href={`/funded/${o.onb}`}>{o.onb}</Link>
                  </th>
                  <td>
                    {formatSize(o.size_cents)} {o.product_id}
                  </td>
                  <td>{STATUS_TEXT[o.status]}</td>
                  <td>{formatDate(o.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function OnboardingPage({ onb }: { onb: string }) {
  const api = useApi();
  const auth = useAuth();
  const [record, setRecord] = useState<Onboarding | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [round, setRound] = useState(0);
  const kyc = useSection("kyc");
  const signedIn = auth.status === "signed_in";
  useEffect(() => {
    if (!signedIn) return;
    let alive = true;
    api.get<Onboarding>(`/me/onboardings/${onb}`).then(
      (found) => {
        if (alive) setRecord(found);
      },
      (e: unknown) => {
        if (alive) setError(e);
      },
    );
    return () => {
      alive = false;
    };
  }, [api, onb, signedIn, round]);
  const reload = () => {
    setRound((r) => r + 1);
  };
  if (!signedIn) {
    return (
      <div className="page">
        <h1>Funded onboarding</h1>
        <Button onClick={() => void auth.signIn(`/funded/${onb}`)}>Sign in to continue</Button>
      </div>
    );
  }
  if (error !== null && record === null) return <Problem error={error} />;
  if (record === null) return <Loading />;
  const current = stepIndex(record.status);
  return (
    <div className="page narrow">
      <p>
        <Link href="/funded">Funded</Link> / {record.onb}
      </p>
      <h1>
        {formatSize(record.size_cents)} {record.product_id} funded account
      </h1>
      <ol className="steps" aria-label="Onboarding steps">
        {STEPS.map((step, i) => (
          <li
            key={step.key}
            className={i < current ? "done" : i === current ? "current" : undefined}
            aria-current={i === current ? "step" : undefined}
          >
            {step.label}
          </li>
        ))}
      </ol>
      {(record.status === "verification" || record.status === "verification_declined") && (
        <VerifyStep record={record} onChange={setRecord} kyc={kyc} />
      )}
      {(record.status === "checks" || record.status === "review") && (
        <Card title={kyc.pending_title ?? "Checks and review"}>
          <p>
            {kyc.pending_text ??
              "Your identity check is done. We screen it and review your account; this usually takes up to one business day. You will see the agreement here when it is ready."}
          </p>
          <Button variant="secondary" onClick={reload}>
            Check again
          </Button>
        </Card>
      )}
      {record.status === "contract" && (
        <Card title="Agreement being prepared">
          <p>Your funded agreement is being prepared.</p>
          <Button variant="secondary" onClick={reload}>
            Check again
          </Button>
        </Card>
      )}
      {record.status === "signature" && <SignStep record={record} onSigned={setRecord} />}
      {(record.status === "signed" || record.status === "provisioned") && (
        <Card title={record.status === "provisioned" ? "Your funded account is open" : "Signed"}>
          {record.acc !== null ? (
            <p>
              Your funded account is <Link href={`/accounts/${record.acc}`}>{record.acc}</Link>. Set your payout wallet
              on <Link href="/payouts">Payouts</Link>.
            </p>
          ) : (
            <>
              <p>Thank you: your account is being opened.</p>
              <Button variant="secondary" onClick={reload}>
                Check again
              </Button>
            </>
          )}
        </Card>
      )}
      {(record.status === "rejected" || record.status === "failed") && (
        <Notice tone="error">
          {record.status === "rejected"
            ? "We could not approve this funded account. Please contact support for the reason."
            : `This onboarding could not be completed${record.failure ? `: ${record.failure}` : ""}.`}
        </Notice>
      )}
    </div>
  );
}

/** The identity check (rule AC15): run at the provider; the words are the
approved `kyc` website section, else these built-in ones. */
function VerifyStep({ record, onChange, kyc }: { record: Onboarding; onChange: (o: Onboarding) => void; kyc: Record<string, string> }) {
  const api = useApi();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const url = record.verification_url ?? null;
  const sandbox = url !== null && url.startsWith("/dev/kyc/");
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.post<Onboarding>(`/me/onboardings/${record.onb}/verification`));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const completeSandbox = async () => {
    if (url === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(url, { result: "approved" });
      onChange(await api.get<Onboarding>(`/me/onboardings/${record.onb}`));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={kyc.required_title ?? "Verify your identity"}>
      {record.status === "verification_declined" && (
        <Notice tone="error">
          {kyc.declined_text ?? "The identity check was declined. Use a valid passport, ID card or driving licence in your own name."}
        </Notice>
      )}
      <p>
        {kyc.required_text ??
          "We check your identity document and a selfie, as regulated firms must. Your name on the document must match your profile."}{" "}
        Attempts used: {record.kyc_attempts} of 3.
      </p>
      {error !== null && <Problem error={error} />}
      {url === null ? (
        <Button busy={busy} onClick={() => void start()}>
          {record.status === "verification_declined" ? "Try again" : "Start identity check"}
        </Button>
      ) : sandbox ? (
        <Button busy={busy} onClick={() => void completeSandbox()}>
          Complete identity check (sandbox)
        </Button>
      ) : (
        <a className="button button-primary" href={url} target="_blank" rel="noopener noreferrer">
          Continue identity check
        </a>
      )}
    </Card>
  );
}

function SignStep({ record, onSigned }: { record: Onboarding; onSigned: (o: Onboarding) => void }) {
  const api = useApi();
  const [contract, setContract] = useState<Contract | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [read, setRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    api.get<Contract>(`/me/onboardings/${record.onb}/contract`).then(
      async (found) => {
        const hash = await sha256Hex(found.body);
        if (!alive) return;
        setContract(found);
        setVerified(hash === found.sha256);
      },
      (e: unknown) => {
        if (alive) setError(e);
      },
    );
    return () => {
      alive = false;
    };
  }, [api, record.onb]);
  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    if (contract === null || verified !== true) return;
    setBusy(true);
    setError(null);
    try {
      onSigned(
        await api.post<Onboarding>(`/me/onboardings/${record.onb}/sign`, {
          full_name: name.trim(),
          sha256: contract.sha256,
        }),
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  if (contract === null) return error !== null ? <Problem error={error} /> : <Loading label="Loading the agreement" />;
  return (
    <Card title="Sign the funded agreement">
      {contract.draft && (
        <Notice tone="error">This agreement is a draft: the firm's legal details are not final yet.</Notice>
      )}
      {verified === false && (
        <Notice tone="error">The agreement did not arrive intact. Reload the page before signing.</Notice>
      )}
      <div className="contract" tabIndex={0} role="region" aria-label="Funded agreement">
        <pre>{contract.body}</pre>
      </div>
      <p className="muted">
        Agreement {contract.contract} ({contract.template}), fingerprint SHA-256 <code>{contract.sha256}</code>
      </p>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <label className="field check">
          <input
            type="checkbox"
            checked={read}
            onChange={(e) => {
              setRead(e.target.checked);
            }}
          />
          <span>I have read the agreement and accept it</span>
        </label>
        <label className="field">
          <span>Your full name, as on your identity document</span>
          <input
            type="text"
            autoComplete="name"
            value={name}
            maxLength={200}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </label>
        {error !== null && <Problem error={error} />}
        <Button type="submit" busy={busy} disabled={!read || name.trim() === "" || verified !== true}>
          Sign agreement
        </Button>
      </form>
    </Card>
  );
}
