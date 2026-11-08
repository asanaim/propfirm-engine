import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ActionButton } from "./ActionButton";
import { when } from "./format";
import { useGet } from "./useGet";
import { monthGrid, plainError } from "./work";

interface Approval {
  what: string;
  ref: string;
  by: string;
  ts: number;
}
interface Pair {
  what: string;
  maker: string;
  checker: string;
  times: number;
}
export interface Anomalies {
  since: number;
  approvals: number;
  out_of_hours: Approval[];
  repeated_pairs: Pair[];
  office: { timezone: string; days: number[]; hours: [number, number] };
}

/** The weekly anomaly report (admins and owners). */
export function AnomalyReport() {
  const { result } = useGet<Anomalies>("/staff/reports/anomalies?days=7");
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const r = result.data;
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="Anomaly report" />
      <Card>
        <CardHeader
          title={`${r.approvals} approvals since ${when(r.since)}`}
          subheader={`Office: ${r.office.hours[0]}:00–${r.office.hours[1]}:00 ${r.office.timezone}`}
        />
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Out of office hours (${r.out_of_hours.length})`} />
        <CardContent>
          <Table size="small" aria-label="out of office hours">
            <TableHead>
              <TableRow>
                <TableCell>What</TableCell>
                <TableCell>Record</TableCell>
                <TableCell>By</TableCell>
                <TableCell>When</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {r.out_of_hours.map((a) => (
                <TableRow key={`${a.what}-${a.ref}-${a.ts}`}>
                  <TableCell>{a.what}</TableCell>
                  <TableCell>{a.ref}</TableCell>
                  <TableCell>{a.by}</TableCell>
                  <TableCell>{when(a.ts)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Same maker and checker again and again (${r.repeated_pairs.length})`} />
        <CardContent>
          <Table size="small" aria-label="repeated pairs">
            <TableHead>
              <TableRow>
                <TableCell>What</TableCell>
                <TableCell>Maker</TableCell>
                <TableCell>Checker</TableCell>
                <TableCell>Times</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {r.repeated_pairs.map((p) => (
                <TableRow key={`${p.what}-${p.maker}-${p.checker}`}>
                  <TableCell>{p.what}</TableCell>
                  <TableCell>{p.maker}</TableCell>
                  <TableCell>{p.checker}</TableCell>
                  <TableCell>{p.times}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </Stack>
  );
}

interface CalendarEvent {
  event_id: string;
  title: string;
  ts: number;
  local: string;
  zone: string;
  scope: string;
  currency: string | null;
  source: string;
  in_engine: boolean;
}
interface Calendar {
  version: string;
  month: string;
  checked: string;
  events: CalendarEvent[];
  unscheduled: { date: string; title: string; currency: string; source: string }[];
}
interface NewsAction {
  id: number;
  action: string;
  event_id: string;
  title: string | null;
  author: string;
  author_email: string | null;
  reason: string;
  result: string;
  detail: string | null;
  ts: number;
}
interface SourceAlert {
  source: string;
  since: number;
  last_error: string;
  last_notice: number | null;
  events: string[];
  page: string;
}
export interface NewsPage {
  calendars: Calendar[];
  impacts: Record<string, Impact>; // marked releases; any other is high
  actions: NewsAction[];
  source_alerts: SourceAlert[];
}

export type Impact = "high" | "medium" | "low";
const IMPACTS: Impact[] = ["high", "medium", "low"];
const IMPACT_WORDS: Record<Impact, string> = { high: "High", medium: "Medium", low: "Low" };
/** Light to deep red, as on the traders' calendar; the words carry the meaning. */
const IMPACT_SX: Record<Impact, { bgcolor: string; color: string }> = {
  high: { bgcolor: "#b42318", color: "#ffffff" },
  medium: { bgcolor: "#c8372d", color: "#ffffff" },
  low: { bgcolor: "#fde3e0", color: "#7a1b12" },
};

/** The releases still to come, each with its impact and a way to mark it
(the risk desk, one person; the restriction itself does not change). */
function ImpactMarks({ n, now, onDone }: { n: NewsPage; now: number; onDone: () => void }) {
  const coming = n.calendars.flatMap((c) => c.events).filter((e) => e.ts > now).sort((a, b) => a.ts - b.ts);
  return (
    <Card>
      <CardHeader
        slotProps={{ title: { component: "h2" } }}
        title="Impact"
        subheader="Mark each release high, medium or low impact for the traders' calendar. Every release here stays restricted."
      />
      <CardContent sx={{ pt: 0 }}>
        {coming.length === 0 ? (
          <Typography color="text.secondary">No release to come.</Typography>
        ) : (
          <Stack spacing={1}>
            {coming.map((e) => {
              const impact = n.impacts[e.event_id] ?? "high";
              return (
                <Stack key={e.event_id} direction="row" sx={{ alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                  <Typography sx={{ flex: 1, minWidth: 240 }}>
                    {when(e.ts)} · {e.title}
                  </Typography>
                  <Chip size="small" label={`${IMPACT_WORDS[impact]} impact`} sx={IMPACT_SX[impact]} />
                  {IMPACTS.filter((i) => i !== impact).map((i) => (
                    <ActionButton
                      key={i}
                      label={`Mark ${IMPACT_WORDS[i].toLowerCase()}`}
                      path={`/staff/news/events/${encodeURIComponent(e.event_id)}/impact`}
                      extra={{ impact: i }}
                      color="inherit"
                      onDone={onDone}
                    />
                  ))}
                </Stack>
              );
            })}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function utcTime(ms: number): string {
  return new Date(ms).toISOString().slice(11, 16);
}

/** One calendar month as a grid (UTC days), each release in its day. */
function MonthGrid({ calendar, now }: { calendar: Calendar; now: number }) {
  const byDay = new Map<number, CalendarEvent[]>();
  for (const e of calendar.events) {
    const d = new Date(e.ts).getUTCDate();
    byDay.set(d, [...(byDay.get(d) ?? []), e]);
  }
  const today = new Date(now).toISOString().slice(0, 7) === calendar.month ? new Date(now).getUTCDate() : null;
  const title = new Date(`${calendar.month}-01T00:00:00Z`).toLocaleString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
  const sent = calendar.events.filter((e) => e.in_engine).length;
  return (
    <Card>
      <CardHeader
        slotProps={{ title: { component: "h2" } }}
        title={title}
        subheader={`${calendar.events.length} releases · ${sent} scheduled in the engine now · file ${calendar.version}, dates checked ${calendar.checked} · times UTC`}
      />
      <CardContent sx={{ pt: 0 }}>
        <div className="month" role="table" aria-label={`${title} restricted releases`}>
          <div className="month-row month-head" role="row">
            {WEEKDAYS.map((d) => (
              <div key={d} role="columnheader">
                {d}
              </div>
            ))}
          </div>
          {monthGrid(calendar.month).map((week, i) => (
            <div className="month-row" role="row" key={i}>
              {week.map((day, j) => (
                <div key={j} role="cell" className={`day${day === null ? " day-empty" : ""}${day !== null && day === today ? " day-today" : ""}`}>
                  {day === null ? null : (
                    <>
                      <span className="day-n">{day}</span>
                      {(byDay.get(day) ?? []).map((e) => (
                        <a
                          key={e.event_id}
                          className={`day-event${e.in_engine ? " in-engine" : ""}${e.ts < now ? " past" : ""}`}
                          href={e.source}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`${e.title}: ${e.local} ${e.zone}${e.in_engine ? " · scheduled in the engine" : ""}`}
                        >
                          <b>{utcTime(e.ts)}</b> {e.title}
                        </a>
                      ))}
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        {calendar.unscheduled.length > 0 ? (
          <Alert severity="info" sx={{ mt: 2 }}>
            No fixed time yet:{" "}
            {calendar.unscheduled.map((u) => `${u.title} (${u.currency}, ${u.date})`).join("; ")}. Add each by hand once the
            publisher sets it.
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** The news desk: sources the collector cannot read (in plain words, with
what to do), each month's releases as a calendar, the desk's latest
actions. Adding or cancelling an event goes through the API
(POST /staff/news/events; an admin cancels). */
export function NewsDesk() {
  const { result, reload } = useGet<NewsPage>("/staff/news");
  const [now] = useState(() => Date.now());
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const n = result.data;
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="News desk" />
      {n.source_alerts.length === 0 ? (
        <Alert severity="success">Every publisher's schedule was read on the last run.</Alert>
      ) : (
        <Card>
          <CardHeader
            slotProps={{ title: { component: "h2" } }}
            title={`Sources not read (${n.source_alerts.length})`}
            subheader="Their releases are not collected automatically: check the publisher's page and add the missing events by hand."
          />
          <CardContent sx={{ pt: 0 }}>
            <Stack spacing={1.5}>
              {n.source_alerts.map((a) => (
                <Alert
                  key={a.source}
                  severity="warning"
                  action={
                    <Button size="small" color="inherit" href={a.page} target="_blank" rel="noopener noreferrer">
                      Open the schedule
                    </Button>
                  }
                >
                  <Typography sx={{ fontWeight: 650 }}>
                    {a.source.toUpperCase()} · not read since {when(a.since)}
                  </Typography>
                  <Typography variant="body2">{plainError(a.last_error)}</Typography>
                  <Stack direction="row" sx={{ flexWrap: "wrap", gap: 0.75, mt: 1 }}>
                    {a.events.map((e) => (
                      <Chip key={e} size="small" variant="outlined" label={e} />
                    ))}
                  </Stack>
                </Alert>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}
      {n.calendars.map((c) => (
        <MonthGrid key={c.month} calendar={c} now={now} />
      ))}
      <ImpactMarks n={n} now={now} onDone={reload} />
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Latest actions (${n.actions.length})`} />
        <CardContent sx={{ pt: 0 }}>
          {n.actions.length === 0 ? (
            <Typography color="text.secondary">None yet.</Typography>
          ) : (
            <Box sx={{ overflowX: "auto" }}>
              <Table size="small" aria-label="news desk actions">
                <TableHead>
                  <TableRow>
                    <TableCell>When</TableCell>
                    <TableCell>Action</TableCell>
                    <TableCell>Event</TableCell>
                    <TableCell>Result</TableCell>
                    <TableCell>By</TableCell>
                    <TableCell>Reason</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {n.actions.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell sx={{ whiteSpace: "nowrap" }}>{when(a.ts)}</TableCell>
                      <TableCell>{a.action}</TableCell>
                      <TableCell>{a.title ?? a.event_id}</TableCell>
                      <TableCell>
                        <Chip size="small" variant="outlined" label={a.result} color={a.result === "done" ? "success" : a.result === "rejected" ? "warning" : "default"} />
                      </TableCell>
                      <TableCell>{a.author_email ?? a.author}</TableCell>
                      <TableCell>{a.reason}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
}

interface AuditRow {
  seq: number;
  ts: number;
  actor: string;
  actor_name: string | null;
  action: string;
  target: string;
  status: number;
  ip: string;
  request_id: string;
  hash: string;
}
export interface AuditPage {
  head: number;
  status: {
    checked_at: number;
    verified_seq: number;
    anchored_seq: number;
    broken_seq: number | null;
    broken_reason: string | null;
  } | null;
  records: AuditRow[];
}

/** The auditor's last check, in words. */
export function auditVerdict(p: AuditPage): { tone: "success" | "warning" | "error"; text: string } {
  const s = p.status;
  if (s === null) return { tone: "warning", text: "Not checked yet: the auditor service has not run." };
  if (s.broken_seq !== null) {
    return { tone: "error", text: `Tampering detected at record ${s.broken_seq}: ${s.broken_reason ?? ""}. Tell the owners.` };
  }
  const behind = p.head - s.verified_seq;
  return {
    tone: "success",
    text:
      `Intact: ${s.verified_seq} records checked ${when(s.checked_at)}, anchored outside the database up to ${s.anchored_seq}` +
      (behind > 0 ? `; ${behind} newer, checked on the next pass.` : "."),
  };
}

/** The security audit trail, newest first (admins and owners). */
export function AuditTrail() {
  const [before, setBefore] = useState<number[]>([]);
  const page = before.at(-1) ?? 0;
  const { result } = useGet<AuditPage>(`/staff/audit?limit=50${page ? `&before=${page}` : ""}`);
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const r = result.data;
  const verdict = auditVerdict(r);
  const last = r.records.at(-1);
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="Security log" />
      <Alert severity={verdict.tone}>{verdict.text}</Alert>
      <Card>
        <CardHeader
          slotProps={{ title: { component: "h2" } }}
          title="Who did what"
          subheader="Every staff and admin request, every refusal, every other change. Hash-chained; nobody can edit it."
        />
        <CardContent>
          <Table size="small" aria-label="security log">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell>
                <TableCell>When</TableCell>
                <TableCell>Who</TableCell>
                <TableCell>Action</TableCell>
                <TableCell>Record</TableCell>
                <TableCell>Result</TableCell>
                <TableCell>Address</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {r.records.map((a) => (
                <TableRow key={a.seq}>
                  <TableCell>{a.seq}</TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{when(a.ts)}</TableCell>
                  <TableCell>{a.actor_name ?? a.actor}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{a.action}</TableCell>
                  <TableCell>{a.target || "-"}</TableCell>
                  <TableCell>
                    <Chip size="small" label={a.status} color={a.status >= 400 ? "error" : "default"} variant="outlined" />
                  </TableCell>
                  <TableCell>{a.ip || "-"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button disabled={before.length === 0} onClick={() => {
                setBefore((b) => b.slice(0, -1));
              }}>
              Newer
            </Button>
            <Button disabled={last === undefined || last.seq <= 1} onClick={() => {
                if (last) setBefore((b) => [...b, last.seq]);
              }}>
              Older
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Stack>
  );
}
