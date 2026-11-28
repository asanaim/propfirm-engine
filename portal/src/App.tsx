import { lazy, Suspense, useEffect, useState, type ComponentType } from "react";
import { useAuth } from "./auth/AuthProvider";
import { linkToken, SubscriptionLinkPage } from "./components/Newsletter";
import { Layout } from "./components/Layout";
import { Loading, Notice } from "./components/ui";
import { OverviewPage } from "./pages/OverviewPage";
import { ShopPage } from "./pages/ShopPage";
import { PolicyPage } from "./pages/PolicyPages";
import { setPrepare, usePath } from "./router";
import { useApi } from "./api/ApiProvider";
import { extensions, install } from "./lib/extensions";

/** A page loaded when first opened (kept light, 12 Nov 2026): the public
home and shop come with the first download; the trading screens and their
chart arrive when a trader opens them. */
/** Each page module's loader, fetched once; `loaded` marks those done. */
const loaders = new Map<() => Promise<unknown>, { once: () => Promise<unknown>; loaded: boolean }>();

function cached<M>(load: () => Promise<M>): () => Promise<M> {
  let found = loaders.get(load);
  if (found === undefined) {
    let pending: Promise<M> | null = null;
    const entry = {
      loaded: false,
      once: (): Promise<M> =>
        (pending ??= load().then((m) => {
          entry.loaded = true;
          return m;
        })),
    };
    found = entry;
    loaders.set(load, entry);
  }
  return found.once as () => Promise<M>;
}

/** The module loader of each page path, for fetching it before a move. */
const byPath = new Map<string, () => Promise<unknown>>();

function later<M extends Record<string, unknown>, P extends object = object>(
  load: () => Promise<M>,
  name: keyof M & string,
): ComponentType<P> {
  const once = cached(load);
  const page = lazy(async () => {
    const component = (await once())[name];
    if (typeof component !== "function") throw new Error(`${name} is not a page`);
    return { default: component as ComponentType<P> };
  });
  (page as unknown as { preload: () => Promise<unknown> }).preload = once;
  return page;
}

function preloadOf(page: unknown): (() => Promise<unknown>) | undefined {
  return (page as { preload?: () => Promise<unknown> }).preload;
}

/** A promise while the page for `path` is still being fetched, else null. */
export function pending(path: string): Promise<unknown> | null {
  const load = byPath.get(path) ?? (path.startsWith("/accounts/") ? byPath.get("/accounts/") : undefined);
  if (load === undefined) return null;
  const entry = [...loaders.values()].find((e) => e.once === load);
  return entry?.loaded === true ? null : load();
}

/** Fetch every page's code in idle time after the first paint, one by one. */
export function preloadPages(): void {
  const all = [...loaders.values()];
  const idle = (next: () => void) => {
    if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(next, { timeout: 2_000 });
    else window.setTimeout(next, 300);
  };
  const step = (i: number) => {
    const entry = all[i];
    if (entry === undefined) return;
    void entry
      .once()
      .catch(() => undefined)
      .finally(() => {
        idle(() => {
          step(i + 1);
        });
      });
  };
  idle(() => {
    step(0);
  });
}

const AccountPage = later<typeof import("./pages/AccountPage"), { acc: string }>(() => import("./pages/AccountPage"), "AccountPage");
const AccountsPage = later(() => import("./pages/AccountsPage"), "AccountsPage");
const FundedPage = later(() => import("./pages/FundedPage"), "FundedPage");
const OnboardingPage = later<typeof import("./pages/FundedPage"), { onb: string }>(() => import("./pages/FundedPage"), "OnboardingPage");
const HistoryPage = later<typeof import("./pages/HistoryPage"), { acc: string }>(() => import("./pages/HistoryPage"), "HistoryPage");
const RulesPage = later<typeof import("./pages/RulesPage"), { acc: string }>(() => import("./pages/RulesPage"), "RulesPage");
const NewsPage = later(() => import("./pages/NewsPage"), "NewsPage");
const NoticesPage = later(() => import("./pages/NoticesPage"), "NoticesPage");
const PayoutsPage = later(() => import("./pages/PayoutsPage"), "PayoutsPage");
const SecurityPage = later(() => import("./pages/SecurityPage"), "SecurityPage");
const ToolsPage = later(() => import("./pages/ToolsPage"), "ToolsPage");
const AnalysisPage = later(() => import("./pages/AnalysisPage"), "AnalysisPage");
const SymbolsPage = later(() => import("./pages/ToolsExtra"), "SymbolsPage");
const ResetPage = later(() => import("./pages/ToolsExtra"), "ResetPage");
const SimulatorPage = later(() => import("./pages/ToolsExtra"), "SimulatorPage");
const ProfilePage = later(() => import("./pages/TraderPages"), "ProfilePage");
const BillingPage = later(() => import("./pages/TraderPages"), "BillingPage");
const InvoicePage = later<typeof import("./pages/TraderPages"), { ord: string }>(() => import("./pages/TraderPages"), "InvoicePage");
const CertificatesPage = later(() => import("./pages/TraderPages"), "CertificatesPage");
const CertificatePage = later<typeof import("./pages/TraderPages"), { id: string }>(() => import("./pages/TraderPages"), "CertificatePage");
const LeaderboardPage = later(() => import("./pages/TraderPages"), "LeaderboardPage");

const PAGES: Record<string, ComponentType> = {
  "/": OverviewPage,
  "/accounts": AccountsPage,
  "/shop": ShopPage,
  "/funded": FundedPage,
  "/payouts": PayoutsPage,
  "/security": SecurityPage,
  "/notices": NoticesPage,
  "/news": NewsPage,
  "/tools": ToolsPage,
  "/analysis": AnalysisPage,
  "/tools/symbols": SymbolsPage,
  "/tools/reset": ResetPage,
  "/tools/simulator": SimulatorPage,
  "/profile": ProfilePage,
  "/billing": BillingPage,
  "/certificates": CertificatesPage,
  "/leaderboard": LeaderboardPage,
};

const ACCOUNT = /^\/accounts\/(ACC[0-9]{8})(\/history|\/rules)?$/;
const ONBOARDING = /^\/funded\/(ONB[0-9]{8})$/;
const LEGAL = /^\/legal\/([a-z0-9][a-z0-9-]{0,39})$/;
const INVOICE = /^\/billing\/(ORD[0-9]{8})$/;
const CERTIFICATE = /^\/certificates\/((?:PRG|PO)[0-9]{8})$/;

for (const [path, page] of Object.entries(PAGES)) {
  const load = preloadOf(page);
  if (load !== undefined) byPath.set(path, load);
}
{
  const account = preloadOf(AccountPage);
  if (account !== undefined) byPath.set("/accounts/", account);
}
setPrepare(pending);

/** Shown only when a page takes more than a moment, so a quick change never
flashes it. */
function Waiting() {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setShown(true);
    }, 250);
    return () => {
      window.clearTimeout(timer);
    };
  }, []);
  return <div className="page-wait">{shown ? <Loading label="Loading" /> : null}</div>;
}

export function App() {
  const auth = useAuth();
  const path = usePath();
  const api = useApi();
  useEffect(() => {
    preloadPages();
  }, []);
  useEffect(() => {
    // live chat and analytics, when switched on (rule AC22)
    void extensions(api).then(install);
  }, [api]);
  const Page = PAGES[path];
  const found = ACCOUNT.exec(path);
  const account = found?.[1];
  const view = found?.[2];
  const onb = ONBOARDING.exec(path)?.[1];
  const legal = LEGAL.exec(path)?.[1];
  const invoice = INVOICE.exec(path)?.[1];
  const certificate = CERTIFICATE.exec(path)?.[1];
  if (path === "/subscribe/confirm" || path === "/unsubscribe") {
    // the newsletter's emailed links are public (rule AC25)
    return (
      <Layout>
        <SubscriptionLinkPage
          action={path === "/unsubscribe" ? "unsubscribe" : "confirm"}
          token={linkToken(window.location.search)}
        />
      </Layout>
    );
  }
  if (legal !== undefined) {
    // policy pages are public: shown signed in or not
    return (
      <Layout>
        <PolicyPage key={legal} slug={legal} />
      </Layout>
    );
  }
  let content: React.JSX.Element;
  if ((path.startsWith("/accounts/") && account === undefined) || (path.startsWith("/funded/") && onb === undefined)) {
    return (
      <Layout>
        <Notice tone="error">This page does not exist.</Notice>
      </Layout>
    );
  }
  if (auth.status === "error") content = <Notice tone="error">Sign-in failed: {auth.error}</Notice>;
  else if (path === "/callback" || auth.status === "loading") content = <Loading label="Signing in" />;
  else if (account !== undefined && view === "/history") content = <HistoryPage key={account} acc={account} />;
  else if (account !== undefined && view === "/rules") content = <RulesPage key={account} acc={account} />;
  else if (account !== undefined) content = <AccountPage key={account} acc={account} />;
  else if (onb !== undefined) content = <OnboardingPage key={onb} onb={onb} />;
  else if (invoice !== undefined) content = <InvoicePage key={invoice} ord={invoice} />;
  else if (certificate !== undefined) content = <CertificatePage key={certificate} id={certificate} />;
  else if (Page === undefined) content = <Notice tone="error">This page does not exist.</Notice>;
  else content = <Page />;
  return (
    <Layout>
      <Suspense fallback={<Waiting />}>{content}</Suspense>
    </Layout>
  );
}
