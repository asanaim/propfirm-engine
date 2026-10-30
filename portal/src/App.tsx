import { useAuth } from "./auth/AuthProvider";
import { Layout } from "./components/Layout";
import { Loading, Notice } from "./components/ui";
import { AccountsPage } from "./pages/AccountsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SecurityPage } from "./pages/SecurityPage";
import { ShopPage } from "./pages/ShopPage";
import { usePath } from "./router";

const PAGES: Record<string, () => React.JSX.Element> = {
  "/": OverviewPage,
  "/accounts": AccountsPage,
  "/shop": ShopPage,
  "/security": SecurityPage,
};

export function App() {
  const auth = useAuth();
  const path = usePath();
  const Page = PAGES[path];
  let content: React.JSX.Element;
  if (auth.status === "error") content = <Notice tone="error">Sign-in failed: {auth.error}</Notice>;
  else if (path === "/callback" || auth.status === "loading") content = <Loading label="Signing in" />;
  else if (Page === undefined) content = <Notice tone="error">This page does not exist.</Notice>;
  else content = <Page />;
  return <Layout>{content}</Layout>;
}
