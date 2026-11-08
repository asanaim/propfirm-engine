/** Which screens a login sees, from its permissions (the API enforces
them again on every request; hiding is only for clarity). */
export function screensFor(permissions: readonly string[]): Set<string> {
  const has = (p: string) => permissions.includes(p);
  const out = new Set(["notifications", "apprentice-proposals"]);
  if (has("staff.view") || has("staff.view_masked")) {
    for (const s of ["payouts", "cases", "onboardings", "progressions"]) out.add(s);
  }
  if (has("news.desk")) out.add("news");
  if (has("staff.directory")) for (const s of ["members", "grants", "cutoffs"]) out.add(s);
  if (has("settings.view")) for (const s of ["proposals", "changes", "anomalies", "audit"]) out.add(s);
  if (has("settings.change")) out.add("policies"); // owners propose; the API checks the tier
  if (has("settings.change")) out.add("site"); // the website's text (AC07)
  if (has("settings.change")) out.add("methods"); // payment methods (AC13)
  return out;
}
