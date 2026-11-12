import { lazy, Suspense, type ComponentType } from "react";
import { useAuth } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { Loading, Notice } from "./components/ui";
import { OverviewPage } from "./pages/OverviewPage";
import { ShopPage } from "./pages/ShopPage";
import { PolicyPage } from "./pages/PolicyPages";
import { usePath } from "./router";

/** A page loaded when first opened (kept light, 12 Nov 2026): the public
home and shop come with the first download; the trading screens and their
chart arrive when a trader opens them. */
function later<M extends Record<string, unknown>, P extends object = object>(
  load: () => Promise<M>,
  name: keyof M & string,
): ComponentType<P> {
  return lazy(async () => {
    const component = (await load())[name];
    if (typeof component !== "function") throw new Error(`${name} is not a page`);
    return { default: component as ComponentType<P> };
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
};

const ACCOUNT = /^\/accounts\/(ACC[0-9]{8})(\/history|\/rules)?$/;
const ONBOARDING = /^\/funded\/(ONB[0-9]{8})$/;
const LEGAL = /^\/legal\/([a-z0-9][a-z0-9-]{0,39})$/;

export function App() {
  const auth = useAuth();
  const path = usePath();
  const Page = PAGES[path];
  const found = ACCOUNT.exec(path);
  const account = found?.[1];
  const view = found?.[2];
  const onb = ONBOARDING.exec(path)?.[1];
  const legal = LEGAL.exec(path)?.[1];
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
  else if (Page === undefined) content = <Notice tone="error">This page does not exist.</Notice>;
  else content = <Page />;
  return (
    <Layout>
      <Suspense fallback={<Loading label="Loading" />}>{content}</Suspense>
    </Layout>
  );
}
