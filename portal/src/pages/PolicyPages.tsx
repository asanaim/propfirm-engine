import { useCallback, useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { Button, Card, Loading, Notice } from "../components/ui";
import { Link } from "../router";

/** A policy page as the API lists it (rule AC10). */
export interface PolicySummary {
  slug: string;
  version: number;
  title: string;
  published_at: number;
}

export interface PolicyText extends PolicySummary {
  body: string;
}

function dated(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

/** One policy page, in force: plain text, shown as written. */
export function PolicyPage({ slug }: { slug: string }) {
  const api = useApi();
  const [page, setPage] = useState<PolicyText | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<PolicyText>(`/policies/${slug}`).then(setPage, () => {
      setError("This page does not exist.");
    });
  }, [api, slug]);
  if (error !== null) return <Notice tone="error">{error}</Notice>;
  if (page === null) return <Loading />;
  return (
    <article className="page narrow policy-page">
      <h1>{page.title}</h1>
      <p className="muted">
        Version {page.version}, in force since {dated(page.published_at)}
      </p>
      <div className="policy-text">{page.body}</div>
    </article>
  );
}

/** Links to the policies in force, for the footer. */
export function PolicyLinks() {
  const api = useApi();
  const [pages, setPages] = useState<PolicySummary[]>([]);
  useEffect(() => {
    api.get<PolicySummary[]>("/policies").then(setPages, () => {
      setPages([]);
    });
  }, [api]);
  return (
    <>
      {pages.map((p) => (
        <Link key={p.slug} href={`/legal/${p.slug}`}>
          {p.title}
        </Link>
      ))}
    </>
  );
}

/** Updated policies a signed-in trader has not accepted yet: shown on every
page until accepted (a new version asks again). */
export function PolicyGate() {
  const api = useApi();
  const auth = useAuth();
  const [pending, setPending] = useState<PolicySummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.get<PolicySummary[]>("/me/policies/pending").then(setPending, () => {
      setPending([]);
    });
  }, [api]);
  useEffect(() => {
    if (auth.status === "signed_in") load();
  }, [auth.status, load]);
  if (auth.status !== "signed_in" || pending.length === 0) return null;
  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const p of pending) await api.post(`/me/policies/${p.slug}/accept`, { version: p.version });
      load();
    } catch {
      setError("Could not record your acceptance. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="policy-gate" role="region" aria-label="Updated policies">
      <Card title="Please review our updated policies">
        <p>Before your next purchase, read and accept:</p>
        <ul>
          {pending.map((p) => (
            <li key={p.slug}>
              <Link href={`/legal/${p.slug}`}>
                {p.title} (version {p.version})
              </Link>
            </li>
          ))}
        </ul>
        {error === null ? null : <Notice tone="error">{error}</Notice>}
        <Button busy={busy} onClick={() => void accept()}>
          I have read and accept these policies
        </Button>
      </Card>
    </div>
  );
}
