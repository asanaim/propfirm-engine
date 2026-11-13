import { LogIn, LogOut, Moon, Sun, UserPlus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";
import { config } from "../config";
import { PolicyGate, PolicyLinks } from "../pages/PolicyPages";
import { Link, usePath } from "../router";
import { FooterContact, MaintenanceBanner, useSiteStatus } from "./SiteStatus";

type Theme = "light" | "dark";
const THEME_KEY = "portal-theme";

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null; // storage blocked: follow the system
  }
}

function useTheme(): [Theme, () => void] {
  const system: Theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  const [theme, setTheme] = useState<Theme>(storedTheme() ?? system);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // not remembered; the choice still applies now
    }
  };
  return [theme, toggle];
}

/** Before sign in: the public site's own sections, as FTMO and FundedNext
show (the trader dashboard's pages appear only after sign in). */
const PUBLIC_NAV = [
  { href: "/shop", label: "Challenges" },
  { href: "/#how", label: "How it works" },
  { href: "/tools", label: "Tools" },
  { href: "/#faq", label: "FAQ" },
];

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/accounts", label: "Accounts" },
  { href: "/shop", label: "Challenges" },
  { href: "/funded", label: "Funded" },
  { href: "/payouts", label: "Payouts" },
  { href: "/news", label: "News" },
  { href: "/analysis", label: "Analysis" },
  { href: "/tools", label: "Tools" },
  { href: "/security", label: "Security" },
];

export function Layout({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const path = usePath();
  const [theme, toggle] = useTheme();
  const site = useSiteStatus();
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" href="/" aria-label={`${config.brand} home`}>
          <img className="logo-on-light" src="/brand/logo.svg" alt="" width="108" height="26" />
          <img className="logo-on-dark" src="/brand/logo-on-dark.svg" alt="" width="108" height="26" />
        </Link>
        <nav aria-label="Main">
          <ul>
            {auth.status === "signed_in"
              ? NAV.map((item) => (
                  <li key={item.href}>
                    <Link href={item.href} aria-current={path === item.href ? "page" : undefined}>
                      {item.label}
                    </Link>
                  </li>
                ))
              : PUBLIC_NAV.map((item) =>
                  item.href.startsWith("/#") ? (
                    <li key={item.href}>
                      <a href={item.href}>{item.label}</a>
                    </li>
                  ) : (
                    <li key={item.href}>
                      <Link href={item.href} aria-current={path === item.href ? "page" : undefined}>
                        {item.label}
                      </Link>
                    </li>
                  ),
                )}
          </ul>
        </nav>
        <div className="topbar-actions">
          <button
            type="button"
            className="icon-button"
            onClick={toggle}
            aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
          >
            {theme === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </button>
          {auth.status === "signed_in" ? (
            <button type="button" className="button button-quiet" onClick={() => void auth.signOut()}>
              <LogOut aria-hidden="true" /> Sign out
            </button>
          ) : (
            <>
              <button type="button" className="button button-quiet" onClick={() => void auth.signIn()}>
                <LogIn aria-hidden="true" /> Sign in
              </button>
              {site.status.registering ? (
                <button type="button" className="button button-primary button-small" onClick={() => void auth.signUp("/")}>
                  <UserPlus aria-hidden="true" /> Get started
                </button>
              ) : null}
            </>
          )}
        </div>
      </header>
      <MaintenanceBanner status={site.status} />
      <main id="main" tabIndex={-1}>
        <PolicyGate />
        {children}
      </main>
      <footer className="footer">
        <p>Trading on this platform is simulated. Nothing here is investment advice.</p>
        <FooterContact contact={site.contact} />
        <p>
          <PolicyLinks />
          <Link href="/notices">Open-source notices</Link>
        </p>
      </footer>
    </>
  );
}
