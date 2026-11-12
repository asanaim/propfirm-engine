import { type ComponentType, lazy } from "react";

/** A screen loaded on first use: `name` is its export in the module. The
Layout's Suspense shows "Loading" until it arrives. */
export function lazyPage<M extends Record<string, unknown>>(load: () => Promise<M>, name: keyof M & string): ComponentType {
  return lazy(async () => {
    const module = await load();
    const component = module[name];
    if (typeof component !== "function") throw new Error(`${name} is not a screen`);
    return { default: component as ComponentType };
  });
}
