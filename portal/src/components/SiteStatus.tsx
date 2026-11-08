import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { SiteSection } from "../pages/OverviewPage";

/** Maintenance and registration in force (rule AC17). */
export interface SiteStatus {
  maintenance: boolean;
  registration_open: boolean;
  registering: boolean;
  selling: boolean;
}

export type ClosedKind = "maintenance" | "registration_closed";

export const OPEN: SiteStatus = { maintenance: false, registration_open: true, registering: true, selling: true };

/** The site status, and the text of the two closed pages as approved.
While loading, or if the status cannot be read, the site counts as open:
the engine refuses closed actions on its own. */
export function useSiteStatus(): { status: SiteStatus; pages: Partial<Record<ClosedKind, SiteSection>> } {
  const api = useApi();
  const [status, setStatus] = useState<SiteStatus>(OPEN);
  const [pages, setPages] = useState<Partial<Record<ClosedKind, SiteSection>>>({});
  useEffect(() => {
    let live = true;
    api.get<SiteStatus>("/site/status").then(
      (s) => {
        if (live) setStatus(s);
      },
      () => undefined,
    );
    api.get<Partial<Record<ClosedKind, SiteSection>>>("/site").then(
      (s) => {
        if (!live) return;
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
  return { status, pages };
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
