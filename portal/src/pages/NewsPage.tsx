import { Clock, ExternalLink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "../api/ApiProvider";
import { Problem } from "../components/Problem";
import { Card, Loading } from "../components/ui";

export interface NewsEvent {
  event_id: string;
  start_ts: number;
  end_ts: number; // the window is [start_ts, end_ts)
  symbols: string[];
  title: string | null; // from the firm's calendar or the risk desk
  release_ts?: number | null; // the publisher's release time, when the calendar names it
  currency?: string | null;
  source?: string | null; // the publisher's schedule page
  impact?: Impact; // marked by the risk desk; high until marked
  restricted?: boolean; // every release on this calendar is
}

export type Impact = "high" | "medium" | "low";

export const IMPACT_LABEL: Record<Impact, string> = {
  high: "High impact",
  medium: "Medium impact",
  low: "Low impact",
};

/** The impact dot and its words (colour is never the only cue). */
export function ImpactMark({ impact = "high" }: { impact?: Impact | undefined }) {
  return <span className={`impact impact-${impact}`} role="img" aria-label={IMPACT_LABEL[impact]} title={IMPACT_LABEL[impact]} />;
}

const UTC = "UTC";

/** The viewer's own time zone, or UTC where the browser does not say. */
export function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || UTC;
  } catch {
    return UTC;
  }
}

/** "2 d 4 h", "3 h 05 m", "4 m 09 s", or "now". */
export function countdown(ms: number): string {
  if (ms <= 0) return "now";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${two(m)} m`;
  return `${m} m ${two(sec)} s`;
}

/** Events grouped by calendar day in `zone`, in time order. */
export function byDay(events: NewsEvent[], zone: string): { day: string; label: string; events: NewsEvent[] }[] {
  const key = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
  const label = new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: "long", day: "numeric", month: "long" });
  const out = new Map<string, { day: string; label: string; events: NewsEvent[] }>();
  for (const e of [...events].sort((a, b) => a.start_ts - b.start_ts)) {
    const at = e.release_ts ?? e.start_ts;
    const day = key.format(at);
    const group = out.get(day) ?? { day, label: label.format(at), events: [] };
    group.events.push(e);
    out.set(day, group);
  }
  return [...out.values()];
}

function useTick(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, ms);
    return () => {
      window.clearInterval(timer);
    };
  }, [ms]);
  return now;
}

function NextUp({ event, zone }: { event: NewsEvent; zone: string }) {
  const now = useTick(1000);
  const at = event.release_ts ?? event.start_ts;
  const inWindow = now >= event.start_ts && now < event.end_ts;
  const when = new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
  return (
    <section className="next-up" aria-labelledby="next-up-title">
      <p className="next-up-kicker" id="next-up-title">
        {inWindow ? "Restricted now" : "Next restricted release"}
      </p>
      <p className="next-up-title">{event.title ?? event.event_id}</p>
      <p className="next-up-count" role="timer" aria-live="off">
        {inWindow ? `ends in ${countdown(event.end_ts - now)}` : countdown(event.start_ts - now)}
      </p>
      <p className="muted">
        {when.format(at)} · {event.symbols.join(", ")}
      </p>
    </section>
  );
}

/** The restricted-events calendar (M13), public: funded accounts may not
open or close a trade on the listed symbols inside each window. Times in
the viewer's own zone, or UTC. */
export function NewsPage() {
  const api = useApi();
  const [events, setEvents] = useState<NewsEvent[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [zone, setZone] = useState(localZone);
  const [symbol, setSymbol] = useState<string | null>(null);
  const now = useTick(30_000);
  useEffect(() => {
    api.get<NewsEvent[]>("/calendar").then(setEvents, setError);
  }, [api]);
  const symbols = useMemo(() => [...new Set((events ?? []).flatMap((e) => e.symbols))].sort(), [events]);
  if (error !== null) return <Problem error={error} />;
  if (events === null) return <Loading />;
  const shown = events.filter((e) => e.end_ts > now && (symbol === null || e.symbols.includes(symbol)));
  const next = shown[0];
  const mine = localZone();
  const time = new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: "2-digit", minute: "2-digit" });
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const tomorrow = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now + 86_400_000);
  return (
    <div className="page news">
      <h1>News calendar</h1>
      <p className="lead">
        Restricted releases, each marked high, medium or low impact by our risk desk. On funded accounts no trade opens or closes on the listed symbols from 2 minutes before to 2
        minutes after each one. Evaluations are not bound.
      </p>
      <details className="explain">
        <summary>Which releases are on it</summary>
        <p className="muted">
          Central bank rate decisions (Federal Reserve, ECB, Bank of England, Bank of Canada, RBA, RBNZ, SNB), FOMC minutes,
          and jobs, inflation and GDP releases for the US, UK, Canada, Australia and New Zealand, plus the weekly US crude
          oil inventories. Each date and time comes from the publisher&apos;s own schedule. Crypto is not restricted.
        </p>
      </details>
      {next !== undefined && <NextUp event={next} zone={zone} />}
      <div className="news-controls">
        <fieldset className="segmented" aria-label="Time zone">
          <legend className="visually-hidden">Time zone</legend>
          {[...new Set([mine, UTC])].map((z) => (
            <label key={z} className="segment">
              <input
                type="radio"
                name="zone"
                checked={zone === z}
                onChange={() => {
                  setZone(z);
                }}
              />
              <span>{z === UTC ? "UTC" : `Your time (${z})`}</span>
            </label>
          ))}
        </fieldset>
        {symbols.length > 1 && (
          <label className="field symbol-filter">
            <span>Symbol</span>
            <select
              value={symbol ?? ""}
              onChange={(e) => {
                setSymbol(e.target.value === "" ? null : e.target.value);
              }}
            >
              <option value="">All symbols</option>
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {shown.length === 0 ? (
        <Card>
          <p>No restricted releases are scheduled{symbol === null ? "" : ` for ${symbol}`}.</p>
        </Card>
      ) : (
        byDay(shown, zone).map((g) => (
          <section key={g.day} className="news-day" aria-label={g.label}>
            <h2>
              {g.label}
              {g.day === today && <span className="day-tag">Today</span>}
              {g.day === tomorrow && <span className="day-tag">Tomorrow</span>}
            </h2>
            <ul className="news-list">
              {g.events.map((e) => (
                <li
                  key={e.event_id}
                  className={["news-item", now >= e.start_ts && now < e.end_ts ? "live" : "", e.restricted === false ? "" : "restricted"]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <time className="news-time" dateTime={new Date(e.release_ts ?? e.start_ts).toISOString()}>
                    {time.format(e.release_ts ?? e.start_ts)}
                  </time>
                  <div className="news-body">
                    <p className="news-title">
                      <ImpactMark impact={e.impact} />
                      {e.title ?? e.event_id}
                      {e.currency && <span className="badge">{e.currency}</span>}
                      <span className={`badge impact-label impact-${e.impact ?? "high"}`}>{IMPACT_LABEL[e.impact ?? "high"]}</span>
                      {e.restricted !== false && <span className="badge restricted-label">Restricted</span>}
                    </p>
                    <p className="news-meta">
                      <Clock aria-hidden="true" className="inline-icon" /> Restricted {time.format(e.start_ts)}–
                      {time.format(e.end_ts)}
                      {e.source && (
                        <>
                          {" · "}
                          <a href={e.source} target="_blank" rel="noopener noreferrer">
                            Publisher&apos;s schedule <ExternalLink aria-hidden="true" className="inline-icon" />
                          </a>
                        </>
                      )}
                    </p>
                    <ul className="symbol-chips" aria-label="Symbols">
                      {e.symbols.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
