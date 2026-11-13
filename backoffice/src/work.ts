/** Pure helpers for the "My work" queue and the news desk. */

export interface TodoLike {
  kind: string;
  since: number;
  due_at: number | null;
}

/** The area a to-do belongs to, for grouping and its icon. */
export function areaOf(kind: string): string {
  if (kind.startsWith("payout_")) return "Payouts";
  if (kind === "case" || kind === "case_decision") return "Cases";
  if (kind === "identity_review" || kind === "funded_review") return "Reviews";
  if (kind === "news_gap") return "News";
  if (kind === "settings_proposal") return "Settings";
  if (kind === "training") return "Training";
  if (kind === "duties") return "Duties";
  if (kind === "note") return "My notes";
  return "Staff";
}

/** "3 d 4 h", "5 h 10 m", "12 m", "now". */
export function age(ms: number): string {
  if (ms < 60_000) return "now";
  const m = Math.floor(ms / 60_000);
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m % 60} m`;
  return `${m} m`;
}

export type Urgency = "overdue" | "due_soon" | "waiting";

/** Overdue, due within a day, or simply waiting. */
export function urgency(t: TodoLike, now: number): Urgency {
  if (t.due_at !== null && t.due_at <= now) return "overdue";
  if (t.due_at !== null && t.due_at - now <= 86_400_000) return "due_soon";
  return "waiting";
}

/** Overdue first, then the soonest due, then the longest waiting. */
export function byPriority<T extends TodoLike>(items: readonly T[], now: number): T[] {
  const rank: Record<Urgency, number> = { overdue: 0, due_soon: 1, waiting: 2 };
  return [...items].sort((a, b) => {
    const r = rank[urgency(a, now)] - rank[urgency(b, now)];
    if (r !== 0) return r;
    if (a.due_at !== null && b.due_at !== null && a.due_at !== b.due_at) return a.due_at - b.due_at;
    if ((a.due_at === null) !== (b.due_at === null)) return a.due_at === null ? 1 : -1;
    return a.since - b.since;
  });
}

/** A collector error in plain words: what happened, not a stack trace. */
export function plainError(raw: string): string {
  const status = /(\d{3}) ([A-Za-z ]+?)'? for url/.exec(raw);
  if (status) {
    const code = status[1] ?? "";
    const reason = (status[2] ?? "").trim();
    if (code === "403") return "The publisher's site refused the collector (403 Forbidden): it blocks automated readers.";
    if (code === "404") return "The schedule page has moved (404 Not Found): its address needs updating.";
    if (code.startsWith("5")) return `The publisher's site had an error (${code} ${reason}); it may recover by itself.`;
    return `The publisher's site answered ${code} ${reason}.`;
  }
  if (/timeout/i.test(raw)) return "The publisher's site did not answer in time.";
  if (/ConnectError|Name or service not known|getaddrinfo/i.test(raw)) return "The collector could not reach the publisher's site.";
  if (/parse|Parse|ValueError/.test(raw)) return "The schedule page changed its layout: the collector could not read the dates.";
  const first = raw.split("\n")[0] ?? raw;
  return first.length > 160 ? `${first.slice(0, 157)}…` : first;
}

/** The weeks (Monday first) of a "YYYY-MM" month, as day numbers or null. */
export function monthGrid(month: string): (number | null)[][] {
  const [y, m] = month.split("-").map(Number);
  const year = y ?? 1970;
  const index = (m ?? 1) - 1;
  const first = new Date(Date.UTC(year, index, 1)).getUTCDay(); // 0 Sunday
  const lead = (first + 6) % 7;
  const days = new Date(Date.UTC(year, index + 1, 0)).getUTCDate();
  const cells: (number | null)[] = [...Array<null>(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)];
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks: (number | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** A notification's text with any raw collector error put in plain words. */
export function plainBody(body: string): string {
  return body.replace(/Error: ([\s\S]*?)(?=\s+These |$)/, (_, raw: string) => plainError(raw));
}
