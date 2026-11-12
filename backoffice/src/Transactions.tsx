import { Alert, Box, Button, Card, CardContent, Chip, MenuItem, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title, usePermissions } from "react-admin";
import { Link } from "react-router-dom";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { money, when } from "./format";
import { useGet } from "./useGet";

export const TYPES = ["purchase", "payout"] as const;
export const STATUSES = ["pending", "paid", "provisioned", "failed", "requested", "approved", "debited", "sent", "denied", "cancelled"] as const;

export interface TxRow {
  ref: string;
  type: (typeof TYPES)[number];
  cif: string;
  acc: string | null;
  amount_cents: number;
  direction: "in" | "out";
  status: string;
  remark: string;
  at: number;
  settled_at: number | null;
}
export interface TxLog {
  rows: TxRow[];
  totals: { count: number; in_cents: number; out_cents: number };
}
export interface EmailRow {
  ntf: string;
  cif: string;
  kind: string;
  subject: string;
  created_at: number;
  delivery: string | null;
}
export interface TxQuery {
  type: string;
  status: string;
  cif: string;
  remark: string;
  from: string; // yyyy-mm-dd, UTC
  to: string; // yyyy-mm-dd, UTC, included
}

const DAY = 86_400_000;

/** A yyyy-mm-dd date as epoch ms at 00:00 UTC; null when empty or invalid. */
export function dayMs(date: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const ms = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(ms) ? null : ms;
}

/** The query string the API takes; the "to" day is included (until the next 00:00 UTC). */
export function txParams(q: TxQuery): string {
  const p = new URLSearchParams();
  if (q.type) p.set("type", q.type);
  if (q.status) p.set("status", q.status);
  if (/^CIF[0-9]{8}$/.test(q.cif.trim().toUpperCase())) p.set("cif", q.cif.trim().toUpperCase());
  if (q.remark.trim().length >= 2) p.set("remark", q.remark.trim().slice(0, 80));
  const since = dayMs(q.from);
  const until = dayMs(q.to);
  if (since !== null) p.set("since", String(since));
  if (until !== null) p.set("until", String(until + DAY));
  return p.toString();
}

const EMPTY: TxQuery = { type: "", status: "", cif: "", remark: "", from: "", to: "" };

/** Reports (rule AC20): the transaction log with filters, totals and CSV
export, and the email log. Proposal and approval history live under
Setting changes and the Security log; login history is kept by Keycloak. */
export function ReportsPage() {
  const [tab, setTab] = useState<"transactions" | "emails">("transactions");
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="Transactions and emails" />
      <Card>
        <Tabs
          value={tab}
          onChange={(_e, v: "transactions" | "emails") => {
            setTab(v);
          }}
          aria-label="Report"
        >
          <Tab value="transactions" label="Transaction log" />
          <Tab value="emails" label="Email log" />
        </Tabs>
      </Card>
      {tab === "transactions" ? <TransactionLog /> : <EmailLog />}
    </Stack>
  );
}

function TransactionLog() {
  const [q, setQ] = useState<TxQuery>(EMPTY);
  const params = txParams(q);
  const { result } = useGet<TxLog>(`/staff/reports/transactions?${params}${params ? "&" : ""}limit=200`);
  const set = (key: keyof TxQuery) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setQ({ ...q, [key]: e.target.value });
  };
  return (
    <>
      <Card>
        <CardContent>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            <TextField select label="Type" value={q.type} onChange={set("type")} size="small" sx={{ minWidth: 140 }}>
              <MenuItem value="">All</MenuItem>
              {TYPES.map((t) => (
                <MenuItem key={t} value={t}>
                  {t}
                </MenuItem>
              ))}
            </TextField>
            <TextField select label="Status" value={q.status} onChange={set("status")} size="small" sx={{ minWidth: 150 }}>
              <MenuItem value="">All</MenuItem>
              {STATUSES.map((s) => (
                <MenuItem key={s} value={s}>
                  {s}
                </MenuItem>
              ))}
            </TextField>
            <TextField label="CIF" value={q.cif} onChange={set("cif")} size="small" placeholder="CIF00000001" />
            <TextField label="Remark" value={q.remark} onChange={set("remark")} size="small" placeholder="product, code, failure" />
            <TextField label="From (UTC)" type="date" value={q.from} onChange={set("from")} size="small" slotProps={{ inputLabel: { shrink: true } }} />
            <TextField label="To (UTC)" type="date" value={q.to} onChange={set("to")} size="small" slotProps={{ inputLabel: { shrink: true } }} />
            <Button
              onClick={() => {
                setQ(EMPTY);
              }}
            >
              Clear
            </Button>
            <ExportButton params={params} />
          </Stack>
        </CardContent>
      </Card>
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? <TxTable log={result.data} /> : null}
    </>
  );
}

function ExportButton({ params }: { params: string }) {
  const { api } = useBackOffice();
  const { permissions } = usePermissions<string[]>();
  const [error, setError] = useState<string | null>(null);
  if (api.text === undefined || !(permissions ?? []).includes("staff.view")) return null;
  const fetchText = api.text;
  const save = async () => {
    setError(null);
    try {
      const csv = await fetchText(`/staff/reports/transactions/export${params ? `?${params}` : ""}`);
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "transactions.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The export failed.");
    }
  };
  return (
    <Box>
      <Button variant="outlined" onClick={() => void save()}>
        Export CSV
      </Button>
      {error ? (
        <Typography role="alert" color="error" variant="body2">
          {error}
        </Typography>
      ) : null}
    </Box>
  );
}

function TxTable({ log }: { log: TxLog }) {
  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", mb: 2 }}>
          <Chip label={`${log.totals.count} transactions`} />
          <Chip color="success" variant="outlined" label={`In (settled): ${money(log.totals.in_cents)}`} />
          <Chip color="warning" variant="outlined" label={`Out (settled): ${money(log.totals.out_cents)}`} />
          {log.rows.length < log.totals.count ? <Chip label={`showing the newest ${log.rows.length}`} /> : null}
        </Stack>
        {log.rows.length === 0 ? (
          <p>No transactions match.</p>
        ) : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small" aria-label="transactions">
              <TableHead>
                <TableRow>
                  <TableCell>When (UTC)</TableCell>
                  <TableCell>Reference</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Trader</TableCell>
                  <TableCell>Account</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Remark</TableCell>
                  <TableCell>Settled</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {log.rows.map((r) => (
                  <TableRow key={r.ref} hover>
                    <TableCell>{when(r.at)}</TableCell>
                    <TableCell>{r.type === "payout" ? <Link to={`/payouts/${r.ref}/show`}>{r.ref}</Link> : r.ref}</TableCell>
                    <TableCell>{r.type}</TableCell>
                    <TableCell>
                      <Link to={`/traders?cif=${r.cif}`}>{r.cif}</Link>
                    </TableCell>
                    <TableCell>{r.acc ?? "-"}</TableCell>
                    <TableCell align="right">{`${r.direction === "out" ? "-" : "+"}${money(r.amount_cents)}`}</TableCell>
                    <TableCell>{r.status}</TableCell>
                    <TableCell>{r.remark || "-"}</TableCell>
                    <TableCell>{when(r.settled_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

function EmailLog() {
  const [delivery, setDelivery] = useState("");
  const { result } = useGet<EmailRow[]>(`/staff/reports/emails?${delivery ? `delivery=${delivery}&` : ""}limit=200`);
  return (
    <Card>
      <CardContent>
        <TextField
          select
          label="Delivery"
          value={delivery}
          onChange={(e) => {
            setDelivery(e.target.value);
          }}
          size="small"
          sx={{ minWidth: 160, mb: 2 }}
        >
          <MenuItem value="">All</MenuItem>
          <MenuItem value="sent">sent</MenuItem>
          <MenuItem value="failed">failed</MenuItem>
          <MenuItem value="waiting">waiting</MenuItem>
        </TextField>
        {result.state === "loading" ? <p>Loading…</p> : null}
        {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
        {result.state === "ready" && result.data.length === 0 ? <p>No emails match.</p> : null}
        {result.state === "ready" && result.data.length > 0 ? (
          <Table size="small" aria-label="emails">
            <TableHead>
              <TableRow>
                <TableCell>When (UTC)</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Trader</TableCell>
                <TableCell>Event</TableCell>
                <TableCell>Subject</TableCell>
                <TableCell>Delivery</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {result.data.map((e) => (
                <TableRow key={e.ntf}>
                  <TableCell>{when(e.created_at)}</TableCell>
                  <TableCell>{e.ntf}</TableCell>
                  <TableCell>
                    <Link to={`/traders?cif=${e.cif}`}>{e.cif}</Link>
                  </TableCell>
                  <TableCell>{e.kind}</TableCell>
                  <TableCell>{e.subject}</TableCell>
                  <TableCell>{e.delivery ?? "waiting"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}
