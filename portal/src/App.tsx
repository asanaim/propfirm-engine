import { useAuth } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { Loading, Notice } from "./components/ui";
import { AccountPage } from "./pages/AccountPage";
import { AccountsPage } from "./pages/AccountsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { NewsPage } from "./pages/NewsPage";
import { NoticesPage } from "./pages/NoticesPage";
import { RulesPage } from "./pages/RulesPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SecurityPage } from "./pages/SecurityPage";
import { ShopPage } from "./pages/ShopPage";
import { usePath } from "./router";

const PAGES: Record<string, () => React.JSX.Element> = {
  "/": OverviewPage,
  "/accounts": AccountsPage,
  "/shop": ShopPage,
  "/security": SecurityPage,
  "/notices": NoticesPage,
  "/news": NewsPage,
};

const ACCOUNT = /^\/accounts\/(ACC[0-9]{8})(\/history|\/rules)?$/;

export function App() {
  const auth = useAuth();
  const path = usePath();
  const Page = PAGES[path];
  const found = ACCOUNT.exec(path);
  const account = found?.[1];
  const view = found?.[2];
  let content: React.JSX.Element;
  if (path.startsWith("/accounts/") && account === undefined) {
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
  else if (Page === undefined) content = <Notice tone="error">This page does not exist.</Notice>;
  else content = <Page />;
  return <Layout>{content}</Layout>;
}
