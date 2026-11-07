/** Pointer and scroll motion for the landing page: CSS 3D driven by custom
properties. Mouse and pen tilt as they move; a finger tilts the element it
presses, until it lifts. Nothing moves for people who prefer reduced
motion, or where the browser lacks the API. */

import { type RefObject, useEffect } from "react";

function reduced(): boolean {
  return typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Sets --rx and --ry (degrees, at most `max`) and --mx and --my (0–100 %)
on `ref` as the pointer moves over `area` (default: the element itself);
back to rest when it leaves. */
export function useTilt(ref: RefObject<HTMLElement | null>, max: number, area?: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    const zone = area?.current ?? el;
    if (el === null || zone === null || reduced()) return;
    let frame = 0;
    const move = (e: PointerEvent) => {
      if (e.pointerType === "touch" && e.type === "pointermove" && e.buttons === 0) return;
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
    const events: [string, (e: PointerEvent) => void][] = [
      ["pointermove", move],
      ["pointerdown", move],
      ["pointerleave", leave],
      [
        "pointerup",
        (e) => {
          if (e.pointerType === "touch") leave();
        },
      ],
      ["pointercancel", leave],
    ];
    for (const [name, fn] of events) zone.addEventListener(name, fn as EventListener, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      for (const [name, fn] of events) zone.removeEventListener(name, fn as EventListener);
    };
  }, [ref, area, max]);
}

/** The entrance (transition plus the longest stagger), in ms. */
export const REVEAL_MS = 900;

/** Elements with data-reveal under `root` rise in when scrolled into view
("wait" → "in", then "done" once the entrance is over, so the staggered
entrance delay never slows later hover motion). Content stays visible if the
observer is missing or motion is reduced: the hidden state is only set
here, never in the markup. */
export function useReveal(root: RefObject<HTMLElement | null>, deps: readonly unknown[]): void {
  useEffect(() => {
    const el = root.current;
    if (el === null || reduced() || typeof IntersectionObserver !== "function") return;
    const timers: number[] = [];
    const items = Array.from(el.querySelectorAll<HTMLElement>("[data-reveal]")).filter((n) => n.dataset.reveal === "");
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (en.isIntersecting) {
            const n = en.target as HTMLElement;
            n.dataset.reveal = "in";
            io.unobserve(n);
            timers.push(
              window.setTimeout(() => {
                n.dataset.reveal = "done";
              }, REVEAL_MS),
            );
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
      for (const t of timers) window.clearTimeout(t);
      for (const n of items) if (n.dataset.reveal !== "") n.dataset.reveal = "done";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run when the revealed content changes
  }, deps);
}
