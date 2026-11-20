import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { SiteSection } from "../pages/OverviewPage";

/** Maintenance and registration in force (rule AC17). */
export interface SiteStatus {
  maintenance: boolean;
  registration_open: boolean;
  registering: boolean;
  selling: boolean;
  /** D-14.23: published when the payout reserve is short, never silent. */
  payouts_delayed?: boolean;
  payouts_delayed_since?: number | null;
}

export type ClosedKind = "maintenance" | "registration_closed";

export const OPEN: SiteStatus = { maintenance: false, registration_open: true, registering: true, selling: true };

/** The site status, and the text of the two closed pages as approved.
While loading, or if the status cannot be read, the site counts as open:
the engine refuses closed actions on its own. */
export function useSiteStatus(): { status: SiteStatus; pages: Partial<Record<ClosedKind, SiteSection>>; contact: Record<string, string> } {
  const api = useApi();
  const [status, setStatus] = useState<SiteStatus>(OPEN);
  const [pages, setPages] = useState<Partial<Record<ClosedKind, SiteSection>>>({});
  const [contact, setContact] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    api.get<SiteStatus>("/site/status").then(
      (s) => {
        if (live) setStatus(s);
      },
      () => undefined,
    );
    api.get<Partial<Record<ClosedKind | "contact", SiteSection>>>("/site").then(
      (s) => {
        if (!live) return;
        setContact(s.contact?.fields ?? {});
        const kept: Partial<Record<ClosedKind, SiteSection>> = {};
        if (s.maintenance !== undefined) kept.maintenance = s.maintenance;
        if (s.registration_closed !== undefined) kept.registration_closed = s.registration_closed;
        setPages(kept);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  return { status, pages, contact };
}

/** The firm's contact details in the footer (set under General in the back
office); empty fields, and the whole line when none is set, are left out. */
export function FooterContact({ contact }: { contact: Record<string, string> }) {
  const name = contact.company_name ?? "";
  const address = contact.address ?? "";
  const email = contact.support_email ?? "";
  if (name === "" && address === "" && email === "") return null;
  return (
    <p className="footer-contact">
      {[name, address].filter((t) => t !== "").join(", ")}
      {email !== "" ? (
        <>
          {name !== "" || address !== "" ? " · " : ""}
          <a href={`mailto:${email}`}>{email}</a>
        </>
      ) : null}
    </p>
  );
}

const DEFAULTS: Record<ClosedKind, { title: string; message: string }> = {
  maintenance: {
    title: "We are doing some maintenance",
    message: "New registrations and challenge purchases are paused for a short while. If you have an account, you can sign in, trade and request payouts as usual.",
  },
  registration_closed: {
    title: "Registration is closed for now",
    message: "We are not taking new traders at the moment. Please come back soon. If you already have an account, sign in as usual.",
  },
};

/** The maintenance or registration closed page, in the approved words when there are any. */
export function ClosedPage({ kind, page }: { kind: ClosedKind; page: SiteSection | undefined }) {
  const fields = page?.fields ?? {};
  const title = fields.title ?? DEFAULTS[kind].title;
  const message = fields.message ?? DEFAULTS[kind].message;
  const image = fields.image ?? "";
  return (
    <section className="closed-page" aria-labelledby="closed-title" data-kind={kind}>
      {image !== "" ? <img className="closed-image" src={image} alt="" /> : null}
      <h1 id="closed-title">{title}</h1>
      <p className="lead">{message}</p>
    </section>
  );
}

/** A line across the top of every page while the site is under maintenance. */
export function MaintenanceBanner({ status }: { status: SiteStatus }) {
  if (!status.maintenance) return null;
  return (
    <div className="maintenance-banner" role="status">
      Maintenance: new registrations and challenge purchases are paused. Sign in, trading and payouts work as usual.
    </div>
  );
}

/** The words shown on the payouts page while payouts are delayed (D-14.23). */
export function payoutDelayText(since: number | null | undefined): string {
  const from = since ? ` since ${new Date(since).toISOString().slice(0, 10)}` : "";
  return `Payouts are taking longer than usual${from}. Every approved payout will be paid, in the order it was requested; you do not need to request again.`;
}

/** One website section's approved fields (empty until approved, or if
/site cannot be read: callers fall back to their own words). */
export function useSection(name: string): Record<string, string> {
  const api = useApi();
  const [fields, setFields] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    api.get<Record<string, SiteSection | undefined>>("/site").then(
      (s) => {
        if (live) setFields(s[name]?.fields ?? {});
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api, name]);
  return fields;
}
