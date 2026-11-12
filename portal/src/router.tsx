import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";
import { flushSync } from "react-dom";

type Transitioning = Document & { startViewTransition?: (update: () => void) => unknown };

function reduced(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Where the page lands after a move: the #section named in the path, or
the top (a new page should not open half way down). */
export function land(path: string): void {
  const hash = path.split("#")[1];
  const target = hash ? document.getElementById(decodeURIComponent(hash)) : null;
  if (target) target.scrollIntoView({ block: "start" });
  else window.scrollTo({ top: 0, left: 0 });
}

/** Moves to a portal path. Where the browser can, the old page fades into
the new one (a view transition: the top bar stays put); otherwise, and for
people who prefer reduced motion, the page changes at once. */
export function navigate(path: string): void {
  const go = () => {
    window.history.pushState(null, "", path);
    flushSync(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    land(path);
  };
  const doc = document as Transitioning;
  const here = window.location.pathname;
  const samePage = path.split("#")[0] === here;
  if (typeof doc.startViewTransition === "function" && !reduced() && !samePage) doc.startViewTransition(go);
  else go();
}

export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => {
      setPath(window.location.pathname);
    };
    window.addEventListener("popstate", update);
    return () => {
      window.removeEventListener("popstate", update);
    };
  }, []);
  return path;
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const click = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={click} {...rest} />;
}
