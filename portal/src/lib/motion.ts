/** Pointer and scroll motion for the landing page. Pure CSS 3D driven by
custom properties; nothing moves for people who prefer reduced motion, on
touch-only devices (tilt) or where the browser lacks the API. */

import { type RefObject, useEffect } from "react";

function reduced(): boolean {
  return typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function finePointer(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/** Sets --rx and --ry (degrees, at most `max`) and --mx and --my (0–100 %)
on `ref` as the pointer moves over `area` (default: the element itself);
back to rest when it leaves. */
export function useTilt(ref: RefObject<HTMLElement | null>, max: number, area?: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    const zone = area?.current ?? el;
    if (el === null || zone === null || reduced() || !finePointer()) return;
    let frame = 0;
    const move = (e: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const r = zone.getBoundingClientRect();
        const x = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
        const y = Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1);
        el.style.setProperty("--ry", `${((x - 0.5) * 2 * max).toFixed(2)}deg`);
        el.style.setProperty("--rx", `${((0.5 - y) * 2 * max).toFixed(2)}deg`);
        el.style.setProperty("--mx", `${(x * 100).toFixed(1)}%`);
        el.style.setProperty("--my", `${(y * 100).toFixed(1)}%`);
        el.dataset.tilt = "on";
      });
    };
    const leave = () => {
      cancelAnimationFrame(frame);
      for (const p of ["--rx", "--ry", "--mx", "--my"]) el.style.removeProperty(p);
      delete el.dataset.tilt;
    };
    zone.addEventListener("pointermove", move);
    zone.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(frame);
      zone.removeEventListener("pointermove", move);
      zone.removeEventListener("pointerleave", leave);
    };
  }, [ref, area, max]);
}

/** Elements with data-reveal under `root` rise in when scrolled into view.
Content stays visible if the observer is missing or motion is reduced: the
hidden state is only set here, never in the markup. */
export function useReveal(root: RefObject<HTMLElement | null>, deps: readonly unknown[]): void {
  useEffect(() => {
    const el = root.current;
    if (el === null || reduced() || typeof IntersectionObserver !== "function") return;
    const items = Array.from(el.querySelectorAll<HTMLElement>("[data-reveal]")).filter((n) => n.dataset.reveal !== "in");
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (en.isIntersecting) {
            (en.target as HTMLElement).dataset.reveal = "in";
            io.unobserve(en.target);
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.12 },
    );
    for (const n of items) {
      n.dataset.reveal = "wait";
      io.observe(n);
    }
    return () => {
      io.disconnect();
      for (const n of items) if (n.dataset.reveal === "wait") n.dataset.reveal = "in";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run when the revealed content changes
  }, deps);
}
