import { Card, CardContent, CardHeader, Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
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
