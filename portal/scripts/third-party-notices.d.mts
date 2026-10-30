export interface Shipped {
  name: string;
  version: string;
  license: string;
  texts: string[];
}
export const PERMISSIVE: Set<string>;
export function collect(root: string): Shipped[];
export function render(packages: Shipped[]): string;
