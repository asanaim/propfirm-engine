/**
 * Every place in the back office, in one catalogue: the menu (rule AC28),
 * the settings hub (rule AC05) and the Ctrl+K search (rule AC06) all read
 * it, so a new screen is added once. Screen keys are those of screensFor;
 * a place is offered only to a login that may open it (the API checks
 * again on every request).
 */
import type { ICONS } from "./icons";

export type IconKey = keyof typeof ICONS;

export interface Place {
  /** The screensFor key that unlocks it; null: every staff login. */
  screen: string | null;
  label: string;
  path: string;
  icon: IconKey;
  /** Other words staff may type for it. */
  keywords: string;
}

/** The menu's places, in menu order. */
export const PLACES: readonly Place[] = [
  { screen: null, label: "My work", path: "/", icon: "work", keywords: "home to do todo dashboard duties training" },
  { screen: "traders", label: "Traders", path: "/traders", icon: "traders", keywords: "customers clients users cif accounts kyc segments" },
  { screen: "transactions", label: "Transactions and emails", path: "/transactions", icon: "transactions", keywords: "transaction log purchases payouts money export csv email log reports" },
  { screen: "payouts", label: "Payouts", path: "/payouts", icon: "payouts", keywords: "withdrawals transactions money paid" },
  { screen: "cases", label: "Cases", path: "/cases", icon: "cases", keywords: "blocks investigations rule breaches" },
  { screen: "onboardings", label: "Identity reviews", path: "/onboardings", icon: "onboardings", keywords: "kyc onboarding identity documents" },
  { screen: "progressions", label: "Funded reviews", path: "/progressions", icon: "progressions", keywords: "funded stage review passed" },
  { screen: null, label: "Apprentice proposals", path: "/apprentice-proposals", icon: "apprenticeProposals", keywords: "apprentice training" },
  { screen: null, label: "Notifications", path: "/notifications", icon: "notifications", keywords: "alerts messages inbox" },
  { screen: "members", label: "Staff", path: "/members", icon: "members", keywords: "members people team employees" },
  { screen: "grants", label: "Access requests", path: "/grants", icon: "grants", keywords: "grants permissions access" },
  { screen: "cutoffs", label: "Leaver cut-offs", path: "/cutoffs", icon: "cutoffs", keywords: "leavers offboarding" },
  { screen: "proposals", label: "Setting proposals", path: "/proposals", icon: "proposals", keywords: "approvals pending four eyes" },
  { screen: "changes", label: "Setting changes", path: "/changes", icon: "changes", keywords: "history settings log" },
  { screen: "settings", label: "System settings", path: "/settings", icon: "settings", keywords: "configuration hub preferences" },
  { screen: "news", label: "News desk", path: "/news", icon: "news", keywords: "calendar economic events impact" },
  { screen: "anomalies", label: "Anomaly report", path: "/anomalies", icon: "anomalies", keywords: "anomalies report unusual" },
  { screen: "audit", label: "Security log", path: "/audit", icon: "audit", keywords: "audit trail log entries" },
  { screen: "methods", label: "Payment methods", path: "/payment-methods", icon: "methods", keywords: "gateways deposits withdrawal methods charges" },
  { screen: "site", label: "Website", path: "/site", icon: "site", keywords: "website content frontend sections hero" },
  { screen: "templates", label: "Notification templates", path: "/notices", icon: "templates", keywords: "email templates wording wrapper messages" },
  { screen: "policies", label: "Policy pages", path: "/policies", icon: "policies", keywords: "terms privacy policy legal" },
];

export type AreaState = "open" | "fixed" | "planned";

/** One card on the settings hub. */
export interface Area {
  key: string;
  title: string;
  /** What the area holds, in plain words. */
  explains: string;
  icon: IconKey;
  state: AreaState;
  /** Where it is edited, when state is "open". */
  path: string | null;
  /** The screensFor key needed to open it, when state is "open". */
  screen: string | null;
  /** The spec rule that builds it, when state is "planned". */
  rule: string | null;
  keywords: string;
}

/** The hub's cards (rule AC05), in its order. */
export const AREAS: readonly Area[] = [
  {
    key: "general",
    title: "General",
    explains: "Site name, base currency (US dollars), the trading day reset at 17:00 New York and versions. Read only: set in the deployment.",
    icon: "general",
    state: "open",
    path: "/general",
    screen: "settings",
    rule: null,
    keywords: "site name currency usd timezone reset day",
  },
  {
    key: "brand",
    title: "Brand",
    explains: "Logo, colours and type used across the portal and the website.",
    icon: "brand",
    state: "planned",
    path: null,
    screen: null,
    rule: "AC30",
    keywords: "logo colours colors fonts tokens",
  },
  {
    key: "notifications",
    title: "Notification templates",
    explains: "The wording of each email the firm sends, and one wrapper around them all. A second person approves. Email only today.",
    icon: "templates",
    state: "open",
    path: "/notices",
    screen: "templates",
    rule: null,
    keywords: "email sms push templates wording",
  },
  {
    key: "payment",
    title: "Payment methods",
    explains: "How traders pay for challenges: limits, charges, instructions and sealed keys. An owner approves every change.",
    icon: "methods",
    state: "open",
    path: "/payment-methods",
    screen: "methods",
    rule: null,
    keywords: "gateways deposits checkout charges",
  },
  {
    key: "withdrawal",
    title: "Withdrawal methods",
    explains: "How traders are paid out. Kept on the payment methods screen; payout requests do not yet apply their limits or charges.",
    icon: "withdrawals",
    state: "open",
    path: "/payment-methods",
    screen: "methods",
    rule: null,
    keywords: "payouts withdraw bank wallet",
  },
  {
    key: "kyc",
    title: "Identity checks (KYC)",
    explains: "Checks run at the identity provider (photo ID and selfie). Edit the words traders see while a check is required, pending or declined, on the Website screen. KYC stays on: no funded account without it.",
    icon: "kyc",
    state: "open",
    path: "/site",
    screen: "site",
    rule: null,
    keywords: "kyc identity form fields documents",
  },
  {
    key: "seo",
    title: "SEO",
    explains: "Title, description and link preview image per public page, with a site wide fallback, on the Website screen (Search and link previews). The sitemap is generated.",
    icon: "seo",
    state: "open",
    path: "/site",
    screen: "site",
    rule: null,
    keywords: "seo meta sitemap search engines",
  },
  {
    key: "languages",
    title: "Languages",
    explains: "Languages and their translated words, English by default.",
    icon: "languages",
    state: "planned",
    path: null,
    screen: null,
    rule: "AC18",
    keywords: "languages translations locale",
  },
  {
    key: "extensions",
    title: "Extensions",
    explains: "Live chat, captcha and analytics, each with sealed keys.",
    icon: "extensions",
    state: "planned",
    path: null,
    screen: null,
    rule: "AC22",
    keywords: "extensions plugins chat captcha analytics",
  },
  {
    key: "offering",
    title: "Products and sizes",
    explains: "Switch products and account sizes on or off for sale, with the $400,000 allocation cap and one Instant account per trader. A second person approves.",
    icon: "offering",
    state: "open",
    path: "/offering",
    screen: "offering",
    rule: null,
    keywords: "products sizes catalogue 10k 25k 50k 100k 200k 400k allocation cap instant classic on sale",
  },
  {
    key: "maintenance",
    title: "Maintenance",
    explains: "Maintenance mode and closing registration, and the page shown for each with its own message and image. A second person approves.",
    icon: "maintenance",
    state: "open",
    path: "/site",
    screen: "site",
    rule: null,
    keywords: "maintenance downtime registration closed",
  },
  {
    key: "security",
    title: "Security",
    explains: "The security switches and what each does. All are locked on in the policy file.",
    icon: "security",
    state: "open",
    path: "/security",
    screen: "settings",
    rule: null,
    keywords: "security mfa switches locked kyc screening",
  },
  {
    key: "system",
    title: "System information",
    explains: "What is running: application version and build, Python, the database and Valkey, and the engine journal. Admins and owners. Updates arrive by a reviewed release, not from here.",
    icon: "system",
    state: "open",
    path: "/system",
    screen: "system",
    rule: null,
    keywords: "version server database valkey python build uptime cache updates",
  },
  {
    key: "website",
    title: "Website",
    explains: "The public website's sections; figures come from product data.",
    icon: "site",
    state: "open",
    path: "/site",
    screen: "site",
    rule: null,
    keywords: "website content frontend",
  },
  {
    key: "policies",
    title: "Policy pages",
    explains: "Terms, privacy and other policies, versioned; traders accept each new version.",
    icon: "policies",
    state: "open",
    path: "/policies",
    screen: "policies",
    rule: null,
    keywords: "terms privacy legal",
  },
];

/** Lower case words of a text. */
const words = (text: string): string[] => text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/**
 * How well an entry matches a query: null when some query word is not the
 * start of any of its words; otherwise lower is better (label hits first).
 */
export function score(query: string, label: string, keywords: string): number | null {
  const wanted = words(query);
  if (wanted.length === 0) return null;
  const inLabel = words(label);
  const all = [...inLabel, ...words(keywords)];
  let total = 0;
  for (const w of wanted) {
    if (inLabel.some((x) => x.startsWith(w))) continue;
    if (all.some((x) => x.startsWith(w))) {
      total += 1;
      continue;
    }
    return null;
  }
  return label.toLowerCase().startsWith(query.trim().toLowerCase()) ? total - 0.5 : total;
}

/** The hub's cards a query keeps (all of them for an empty query). */
export function filterAreas(query: string, areas: readonly Area[] = AREAS): Area[] {
  if (words(query).length === 0) return [...areas];
  return areas.filter((a) => score(query, a.title, `${a.keywords} ${a.explains}`) !== null);
}

/** Whether a login may open an area's screen. */
export function canOpen(area: Area, screens: ReadonlySet<string>): boolean {
  return area.state === "open" && area.path !== null && (area.screen === null || screens.has(area.screen));
}

/** One line in the Ctrl+K results. */
export interface Hit {
  label: string;
  /** What kind of thing it is, shown beside the label. */
  group: "Record" | "Page" | "Setting";
  path: string;
  icon: IconKey;
}

const RECORDS: readonly { prefix: string; screen: string; label: string; icon: IconKey; path: (ref: string) => string }[] = [
  { prefix: "PO", screen: "payouts", label: "Payout", icon: "payouts", path: (r) => `/payouts/${r}/show` },
  { prefix: "CAS", screen: "cases", label: "Case", icon: "cases", path: (r) => `/cases/${r}/show` },
  { prefix: "ONB", screen: "onboardings", label: "Identity review", icon: "onboardings", path: (r) => `/onboardings/${r}/show` },
  { prefix: "PRG", screen: "progressions", label: "Funded review", icon: "progressions", path: (r) => `/progressions/${r}/show` },
  {
    prefix: "CIF",
    screen: "cases",
    label: "Cases of customer",
    icon: "cases",
    path: (r) => `/cases?filter=${encodeURIComponent(JSON.stringify({ cif: r }))}`,
  },
];

/** A typed record reference (prefix and 8 digits) as a hit, or null. */
export function recordHit(query: string, screens: ReadonlySet<string>): Hit | null {
  const ref = query.trim().toUpperCase();
  const m = /^(PO|CAS|ONB|PRG|CIF)([0-9]{8})$/.exec(ref);
  if (m === null) return null;
  const kind = RECORDS.find((r) => r.prefix === m[1]);
  if (kind === undefined || !screens.has(kind.screen)) return null;
  return { label: `${kind.label} ${ref}`, group: "Record", path: kind.path(ref), icon: kind.icon };
}

/** The Ctrl+K results for a query, best first, at most `limit`. */
export function search(query: string, screens: ReadonlySet<string>, limit = 8): Hit[] {
  const record = recordHit(query, screens);
  const ranked: { hit: Hit; score: number; order: number }[] = [];
  PLACES.forEach((p, order) => {
    if (p.screen !== null && !screens.has(p.screen)) return;
    const s = score(query, p.label, p.keywords);
    if (s !== null) ranked.push({ hit: { label: p.label, group: "Page", path: p.path, icon: p.icon }, score: s, order });
  });
  AREAS.forEach((a, i) => {
    if (!canOpen(a, screens) || a.path === null) return;
    const s = score(query, a.title, a.keywords);
    if (s !== null) ranked.push({ hit: { label: a.title, group: "Setting", path: a.path, icon: a.icon }, score: s + 0.25, order: PLACES.length + i });
  });
  ranked.sort((x, y) => x.score - y.score || x.order - y.order);
  const seen = new Set<string>();
  const hits: Hit[] = record === null ? [] : [record];
  for (const r of ranked) {
    const k = `${r.hit.label}|${r.hit.path}`;
    if (seen.has(k)) continue;
    seen.add(k);
    hits.push(r.hit);
  }
  return hits.slice(0, limit);
}
