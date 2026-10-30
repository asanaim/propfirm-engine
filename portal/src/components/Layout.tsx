import { LogIn, LogOut, Moon, Sun } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";
import { config } from "../config";
import { Link, usePath } from "../router";

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

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/accounts", label: "Accounts" },
  { href: "/shop", label: "Challenges" },
  { href: "/security", label: "Security" },
];

export function Layout({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const path = usePath();
  const [theme, toggle] = useTheme();
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" href="/">
          {config.brand}
        </Link>
        <nav aria-label="Main">
          <ul>
            {NAV.map((item) => (
              <li key={item.href}>
                <Link href={item.href} aria-current={path === item.href ? "page" : undefined}>
                  {item.label}
                </Link>
              </li>
            ))}
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
            <button type="button" className="button button-quiet" onClick={() => void auth.signIn()}>
              <LogIn aria-hidden="true" /> Sign in
            </button>
          )}
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <footer className="footer">
        <p>Trading on this platform is simulated. Nothing here is investment advice.</p>
        <p>
          <Link href="/notices">Open-source notices</Link>
        </p>
      </footer>
    </>
  );
}
