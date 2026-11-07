import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { when } from "./format";
import { useGet } from "./useGet";

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

interface NewsPage {
  calendars: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  source_alerts: Record<string, unknown>[];
}

/** The news desk, read-only here: calendars, the desk's latest actions and
sources the collector cannot read. Adding or cancelling an event goes
through the API (POST /staff/news/events; an admin cancels). */
export function NewsDesk() {
  const { result } = useGet<NewsPage>("/staff/news");
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const n = result.data;
  const block = (title: string, rows: Record<string, unknown>[]) => (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title={`${title} (${rows.length})`} />
      <CardContent>
        <pre className="json">{JSON.stringify(rows, null, 2)}</pre>
      </CardContent>
    </Card>
  );
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="News desk" />
      {block("Sources not read", n.source_alerts)}
      {block("Calendars", n.calendars)}
      {block("Latest actions", n.actions)}
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
