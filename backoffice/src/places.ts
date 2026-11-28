/**
 * Every place in the back office, in one catalogue: the menu (rule AC28),
 * the settings hub (rule AC05) and the Ctrl+K search (rule AC06) all read
 * it, so a new screen is added once. Screen keys are those of screensFor;
 * a place is offered only to a login that may open it (the API checks
 * again on every request).
 */
import type { ICONS } from "./icons";

export type IconKey = keyof typeof ICONS;

/** The menu's sections (rule AC28: expandable groups), in menu order. */
export const GROUPS = [
  { key: "traders", label: "Traders" },
  { key: "finance", label: "Finance" },
  { key: "risk", label: "Risk" },
  { key: "team", label: "Team" },
  { key: "settings", label: "Settings" },
] as const;
export type GroupKey = (typeof GROUPS)[number]["key"];

export interface Place {
  /** The menu section it sits in; null: at the top, always in view. */
  group: GroupKey | null;
  /** The screensFor key that unlocks it; null: every staff login. */
  screen: string | null;
  label: string;
  path: string;
  icon: IconKey;
  /** Other words staff may type for it. */
  keywords: string;
  /** Kept in the top bar instead of the menu (still found by Ctrl+K). */
  bar?: true;
}

/** The menu's places, in menu order within each section. */
export const PLACES: readonly Place[] = [
  { group: null, screen: null, label: "My work", path: "/", icon: "work", keywords: "home to do todo dashboard duties training" },
  { group: null, screen: null, label: "Notifications", path: "/notifications", icon: "notifications", keywords: "alerts messages inbox bell", bar: true },
  { group: null, screen: null, label: "Profile", path: "/profile", icon: "traders", keywords: "me account password authenticator my grants roles", bar: true },
  { group: "traders", screen: "traders", label: "Traders", path: "/traders", icon: "traders", keywords: "customers clients users cif accounts kyc segments" },
  { group: "traders", screen: "onboardings", label: "Identity reviews", path: "/onboardings", icon: "onboardings", keywords: "kyc onboarding identity documents" },
  { group: "traders", screen: "progressions", label: "Funded reviews", path: "/progressions", icon: "progressions", keywords: "funded stage review passed" },
  { group: "traders", screen: "cases", label: "Cases", path: "/cases", icon: "cases", keywords: "blocks investigations rule breaches" },
  { group: "finance", screen: "progressions", label: "Scaling", path: "/scaling", icon: "scaling", keywords: "scale allocation step grow account size" },
  { group: "finance", screen: "settings", label: "Coupons", path: "/coupons", icon: "coupons", keywords: "discount code coupon promo voucher" },
  { group: "finance", screen: "settings", label: "Liabilities", path: "/liabilities", icon: "liabilities", keywords: "liability obligations payouts owed exposure claim reserve coverage sweep" },
  { group: "finance", screen: "settings", label: "Exposure", path: "/exposure", icon: "exposure", keywords: "exposure scenario stress symbol bloc lot cap cut risk desk reserve" },
  { group: "finance", screen: "settings", label: "Transfers to send", path: "/transfers", icon: "transfers", keywords: "send payout transfer transaction hash manual wallet withdrawal" },
  { group: "finance", screen: "settings", label: "Manual receipts", path: "/receipts", icon: "receipts", keywords: "bank transfer manual payment receipt confirm wire deposit" },
  { group: "finance", screen: "settings", label: "Refunds and chargebacks", path: "/disputes", icon: "disputes", keywords: "refund chargeback dispute return money bank reversal" },
  { group: "finance", screen: "settings", label: "Reconciliation", path: "/reconciliation", icon: "reconciliation", keywords: "reconcile statement processor settlement match unmatched missing csv" },
  { group: "finance", screen: "settings", label: "Books", path: "/books", icon: "books", keywords: "ledger accounting trial balance expense income bookkeeping" },
  { group: "finance", screen: "settings", label: "Referrals", path: "/referrals", icon: "referrals", keywords: "referral affiliate commission clawback ledger" },
  { group: "finance", screen: "payouts", label: "Payouts", path: "/payouts", icon: "payouts", keywords: "withdrawals transactions money paid" },
  { group: "finance", screen: "transactions", label: "Transactions and emails", path: "/transactions", icon: "transactions", keywords: "transaction log purchases payouts money export csv email log reports" },
  { group: "finance", screen: "methods", label: "Payment methods", path: "/payment-methods", icon: "methods", keywords: "gateways deposits withdrawal methods charges" },
  { group: "risk", screen: "news", label: "News desk", path: "/news", icon: "news", keywords: "calendar economic events impact" },
  { group: "risk", screen: "cases", label: "Identity clusters", path: "/clusters", icon: "clusters", keywords: "cluster linked multi accounting same person wallet identity copy trading" },
  { group: "risk", screen: "cases", label: "Trader classes", path: "/classes", icon: "classes", keywords: "class a b c d proven watch restricted risk profile review" },
  { group: "risk", screen: "cases", label: "Cohort alarm", path: "/cohorts", icon: "cohorts", keywords: "cohort pass rate zero edge bound affiliate exploit binomial hold" },
  { group: "risk", screen: "cases", label: "Price corrections", path: "/corrections", icon: "corrections", keywords: "bad price spike tick reverse reinstate breach correction" },
  { group: "risk", screen: "anomalies", label: "Anomaly report", path: "/anomalies", icon: "anomalies", keywords: "anomalies report unusual" },
  { group: "risk", screen: "settings", label: "Data erasure", path: "/erasures", icon: "erasures", keywords: "erasure erase gdpr personal data privacy forget retention owner delete" },
  { group: "risk", screen: "audit", label: "Security log", path: "/audit", icon: "audit", keywords: "audit trail log entries" },
  { group: "team", screen: "members", label: "Staff", path: "/members", icon: "members", keywords: "members people team employees" },
  { group: "team", screen: "grants", label: "Access requests", path: "/grants", icon: "grants", keywords: "grants permissions access" },
  { group: "team", screen: "cutoffs", label: "Leaver cut-offs", path: "/cutoffs", icon: "cutoffs", keywords: "leavers offboarding" },
  { group: "team", screen: null, label: "Apprentice proposals", path: "/apprentice-proposals", icon: "apprenticeProposals", keywords: "apprentice training" },
  { group: "settings", screen: "settings", label: "System settings", path: "/settings", icon: "settings", keywords: "configuration hub preferences" },
  { group: "settings", screen: "proposals", label: "Setting proposals", path: "/proposals", icon: "proposals", keywords: "approvals pending four eyes" },
  { group: "settings", screen: "changes", label: "Setting changes", path: "/changes", icon: "changes", keywords: "history settings log" },
  { group: "settings", screen: "site", label: "Website", path: "/site", icon: "site", keywords: "website content frontend sections hero" },
  { group: "settings", screen: "settings", label: "Subscribers", path: "/subscribers", icon: "subscribers", keywords: "newsletter subscribers consent email list marketing export" },
  { group: "settings", screen: "settings", label: "Newsletter", path: "/newsletter", icon: "newsletter", keywords: "newsletter campaign email send marketing broadcast" },
  { group: "settings", screen: "templates", label: "Notification templates", path: "/notices", icon: "templates", keywords: "email templates wording wrapper messages" },
  { group: "settings", screen: "policies", label: "Policy pages", path: "/policies", icon: "policies", keywords: "terms privacy policy legal" },
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
    explains: "The firm's contact details shown on the website (editable, a second person approves), and the fixed basics: site name, base currency, the 17:00 New York reset and versions.",
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
    path: "/site?part=kyc",
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
    path: "/site?part=seo",
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
    explains: "Live chat (Crisp), captcha (Cloudflare Turnstile) and analytics (Cloudflare Web Analytics), all free. Secrets are encrypted at once. A second person approves; an owner approves a secret.",
    icon: "extensions",
    state: "open",
    path: "/extensions",
    screen: "settings",
    rule: null,
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
    path: "/site?part=maintenance",
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

/** Whether the letters of `w` appear in `x` in order ("pyt" in "payout"). */
function inOrder(w: string, x: string): boolean {
  let i = 0;
  for (const ch of x) if (ch === w[i]) i++;
  return i === w.length;
}

/**
 * How well an entry matches a query: null when some query word matches
 * nothing; otherwise lower is better. Per query word, best first: the start
 * of a label word (0), the start of a keyword (1), inside a word (2), its
 * letters in order in a label word, for typos such as "pyouts" (3, words of
 * three letters or more). A label that starts with the whole query gains.
 */
export function score(query: string, label: string, keywords: string): number | null {
  const wanted = words(query);
  if (wanted.length === 0) return null;
  const inLabel = words(label);
  const all = [...inLabel, ...words(keywords)];
  let total = 0;
  for (const w of wanted) {
    if (inLabel.some((x) => x.startsWith(w))) continue;
    if (all.some((x) => x.startsWith(w))) total += 1;
    else if (w.length >= 3 && all.some((x) => x.includes(w))) total += 2;
    else if (w.length >= 3 && inLabel.some((x) => x[0] === w[0] && inOrder(w, x))) total += 3;
    else return null;
  }
  return label.toLowerCase().startsWith(query.trim().toLowerCase()) ? total - 0.5 : total;
}

const RECENT_KEY = "pipridge.search.recent";
const RECENT_MAX = 5;

/** The pages last opened from the search, newest first (this browser only). */
export function recentHits(): Hit[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const list: unknown = raw === null ? [] : JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    return list.filter((h): h is Hit => typeof h === "object" && h !== null && "path" in h && "label" in h && "icon" in h).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

/** Keep a hit opened from the search at the top of the recent list. */
export function rememberHit(hit: Hit): void {
  try {
    const kept = [hit, ...recentHits().filter((h) => h.path !== hit.path)].slice(0, RECENT_MAX);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(kept));
  } catch {
    // storage refused (private mode): the search still works without it
  }
}

/** Shown before anything is typed, after the recent pages. */
const STARTERS = ["/", "/traders", "/payouts", "/cases", "/settings"];

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
  group: "Record" | "Page" | "Setting" | "Recent" | "Trader";
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

/** The page of a record reference (prefix and 8 digits), or null for a
reference no page shows. */
export function recordPath(ref: string | null): string | null {
  if (ref === null) return null;
  const m = /^(PO|CAS|ONB|PRG|CIF)([0-9]{8})$/.exec(ref);
  if (m === null) return null;
  return RECORDS.find((r) => r.prefix === m[1])?.path(ref) ?? null;
}

/** Suggestions for an empty query: recent pages, then common ones. */
export function starters(screens: ReadonlySet<string>, recent: readonly Hit[] = recentHits(), limit = 8): Hit[] {
  const out: Hit[] = recent.map((h) => ({ ...h, group: "Recent" }));
  for (const path of STARTERS) {
    const p = PLACES.find((x) => x.path === path);
    if (p === undefined || (p.screen !== null && !screens.has(p.screen))) continue;
    if (out.some((h) => h.path === p.path)) continue;
    out.push({ label: p.label, group: "Page", path: p.path, icon: p.icon });
  }
  return out.slice(0, limit);
}

/** The Ctrl+K results for a query, best first, at most `limit`. */
export function search(query: string, screens: ReadonlySet<string>, limit = 8): Hit[] {
  if (words(query).length === 0) return starters(screens, recentHits(), limit);
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
