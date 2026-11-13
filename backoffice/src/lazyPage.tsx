import { type ComponentType, lazy } from "react";

const loaders: (() => Promise<unknown>)[] = [];

/** A screen loaded on first use: `name` is its export in the module. Every
screen is also fetched ahead in idle time (`preloadScreens`), so moving
between pages does not wait for the network. */
export function lazyPage<M extends Record<string, unknown>>(
  load: () => Promise<M>,
  name: keyof M & string,
): ComponentType {
  let pending: Promise<M> | null = null;
  const once = (): Promise<M> => (pending ??= load());
  loaders.push(once);
  return lazy(async () => {
    const module = await once();
    const component = module[name];
    if (typeof component !== "function")
      throw new Error(`${name} is not a screen`);
    return { default: component as ComponentType };
  });
}

/** Fetch every screen's code, one after another, while the browser is idle.
Failures are left for the screen's own load to report. */
export function preloadScreens(): () => void {
  let stopped = false;
  const idle = (next: () => void): void => {
    if (typeof window.requestIdleCallback === "function")
      window.requestIdleCallback(next, { timeout: 2_000 });
    else window.setTimeout(next, 200);
  };
  const step = (i: number): void => {
    if (stopped || i >= loaders.length) return;
    const load = loaders[i];
    if (load === undefined) return;
    void load()
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
  return () => {
    stopped = true;
  };
}

/** How many screens are registered (tests). */
export function screenCount(): number {
  return loaders.length;
}
