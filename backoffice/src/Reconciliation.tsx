import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

export const STATUSES = ["matched", "amount_differs", "not_settled", "unmatched", "repeated"] as const;
export type Status = (typeof STATUSES)[number];

/** What each status means, and whether it needs a look. */
export const STATUS: Record<Status, { label: string; color: "success" | "warning" | "error" | "default"; note: string }> = {
  matched: { label: "Matched", color: "success", note: "the record exists, is settled and has this amount" },
  amount_differs: { label: "Amount differs", color: "error", note: "settled, with another amount" },
  not_settled: { label: "Not settled", color: "warning", note: "the record is not paid or sent yet" },
  unmatched: { label: "Unmatched", color: "error", note: "no record has this reference" },
  repeated: { label: "Repeated", color: "warning", note: "the reference is on an earlier line" },
};

interface Run {
  run: string;
  provider: string;
  covers: "in" | "out" | "both";
  period_from: number;
  period_to: number;
  filename: string;
  uploaded_by: string;
  uploaded_at: number;
  lines: number;
  in_cents: number;
  out_cents: number;
  fee_cents: number;
  counts: Record<Status, number>;
}

interface Item {
  line: number;
  occurred_at: number;
  reference: string;
  direction: "in" | "out";
  amount_cents: number;
  fee_cents: number;
  currency: string;
  status: Status;
  record: string | null;
  expected_cents: number | null;
}

interface Missing {
  kind: "order" | "payout";
  record: string;
  reference: string;
  amount_cents: number;
  at: number;
}

export interface Detail extends Run {
  items: Item[];
  missing: Missing[];
}

/** A UTC calendar day (YYYY-MM-DD) as epoch ms at 00:00 UTC; null if not a day. */
export function dayMs(day: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const ms = Date.parse(`${day}T00:00:00Z`);
  return Number.isNaN(ms) ? null : ms;
}

/** Lines that need a look: everything but matched. */
export function problems(d: Detail): Item[] {
  return d.items.filter((i) => i.status !== "matched");
}

function StatusChip({ status }: { status: Status }) {
  const s = STATUS[status];
  return <Chip size="small" label={s.label} color={s.color} variant={status === "matched" ? "outlined" : "filled"} />;
}

function RunDetail({ run }: { run: string }) {
  const { result } = useGet<Detail>(`/staff/reconciliations/${run}`);
  const [all, setAll] = useState(false);
  if (result.state === "loading") return <Typography color="text.secondary">Loading…</Typography>;
  if (result.state === "error") return <Alert severity="error">{result.message}</Alert>;
  const d = result.data;
  const shown = all ? d.items : problems(d);
  return (
    <Stack spacing={2}>
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        {STATUSES.map((s) => (
          <Chip key={s} label={`${STATUS[s].label}: ${String(d.counts[s])}`} color={d.counts[s] && s !== "matched" ? STATUS[s].color : "default"} variant="outlined" />
        ))}
        <Chip label={`Missing from the statement: ${String(d.missing.length)}`} color={d.missing.length ? "error" : "default"} variant="outlined" />
      </Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="subtitle2">{all ? "Every line" : "Lines that need a look"}</Typography>
        <Button
          size="small"
          onClick={() => {
            setAll(!all);
          }}
        >
          {all ? "Show only problems" : `Show all ${String(d.items.length)} lines`}
        </Button>
      </Box>
      {shown.length === 0 ? (
        <Typography color="text.secondary">Every line matched.</Typography>
      ) : (
        <Table size="small" aria-label="Statement lines">
          <TableHead>
            <TableRow>
              <TableCell>Line</TableCell>
              <TableCell>When</TableCell>
              <TableCell>Reference</TableCell>
              <TableCell>Direction</TableCell>
              <TableCell align="right">Amount</TableCell>
              <TableCell align="right">Fee</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Record</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((i) => (
              <TableRow key={i.line}>
                <TableCell>{i.line}</TableCell>
                <TableCell>{when(i.occurred_at)}</TableCell>
                <TableCell sx={{ fontFamily: "monospace" }}>{i.reference}</TableCell>
                <TableCell>{i.direction}</TableCell>
                <TableCell align="right">
                  {usd(i.amount_cents)} {i.currency}
                  {i.expected_cents !== null && i.expected_cents !== i.amount_cents ? (
                    <Typography variant="caption" color="error" sx={{ display: "block" }}>
                      record {usd(i.expected_cents)}
                    </Typography>
                  ) : null}
                </TableCell>
                <TableCell align="right">{usd(i.fee_cents)}</TableCell>
                <TableCell title={STATUS[i.status].note}>
                  <StatusChip status={i.status} />
                </TableCell>
                <TableCell sx={{ fontFamily: "monospace" }}>{i.record ?? "-"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {d.missing.length > 0 ? (
        <>
          <Typography variant="subtitle2">Settled in the period, not on the statement</Typography>
          <Table size="small" aria-label="Missing records">
            <TableHead>
              <TableRow>
                <TableCell>Record</TableCell>
                <TableCell>Reference</TableCell>
                <TableCell>Settled</TableCell>
                <TableCell align="right">Amount</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {d.missing.map((m) => (
                <TableRow key={m.record}>
                  <TableCell sx={{ fontFamily: "monospace" }}>{m.record}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{m.reference}</TableCell>
                  <TableCell>{when(m.at)}</TableCell>
                  <TableCell align="right">{usd(m.amount_cents)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      ) : null}
    </Stack>
  );
}

/** Reconciliation (D-14.21): a processor statement matched line by line to
orders and payouts; anything unmatched or missing is listed. */
export function ReconciliationPage() {
  const { api } = useBackOffice();
  const runs = useGet<Run[]>("/staff/reconciliations");
  const [form, setForm] = useState({ provider: "", covers: "both", from: "", to: "" });
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const from = dayMs(form.from);
  const to = dayMs(form.to);
  const ready = form.provider.trim() !== "" && from !== null && to !== null && to > from && file !== null && !busy;
  const upload = async () => {
    if (!ready) return;
    setBusy(true);
    setMessage(null);
    try {
      // the period's last day is included: it ends at the next midnight UTC
      const made = await api.post<Run>("/staff/reconciliations", {
        provider: form.provider.trim(),
        covers: form.covers,
        period_from: from,
        period_to: to + 86_400_000,
        filename: file.name,
        csv: file.text,
      });
      setMessage({ ok: true, text: `${made.run}: ${String(made.lines)} lines read, ${String(made.counts.matched)} matched.` });
      setOpen(made.run);
      setFile(null);
      runs.reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The upload failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Reconciliation" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="Upload a statement" subheader="CSV with the header date,reference,direction,amount,currency and an optional fee. Times carry a zone; amounts are dollars with at most two decimals. A file with any bad line is refused whole." />
        <CardContent>
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", md: "repeat(4, 1fr)" }, alignItems: "center" }}>
            <TextField
              label="Provider"
              size="small"
              value={form.provider}
              helperText="as on the orders, e.g. nowpayments"
              onChange={(e) => {
                setForm({ ...form, provider: e.target.value.toLowerCase() });
              }}
            />
            <TextField
              select
              label="Covers"
              size="small"
              value={form.covers}
              helperText="which records it should list"
              onChange={(e) => {
                setForm({ ...form, covers: e.target.value });
              }}
            >
              <MenuItem value="both">Money in and out</MenuItem>
              <MenuItem value="in">Money in: sales</MenuItem>
              <MenuItem value="out">Money out: payouts</MenuItem>
            </TextField>
            <TextField
              label="From (UTC)"
              type="date"
              size="small"
              value={form.from}
              slotProps={{ inputLabel: { shrink: true } }}
              onChange={(e) => {
                setForm({ ...form, from: e.target.value });
              }}
            />
            <TextField
              label="To, included (UTC)"
              type="date"
              size="small"
              value={form.to}
              slotProps={{ inputLabel: { shrink: true } }}
              onChange={(e) => {
                setForm({ ...form, to: e.target.value });
              }}
            />
          </Box>
          <Box sx={{ display: "flex", gap: 2, alignItems: "center", mt: 2, flexWrap: "wrap" }}>
            <Button variant="outlined" component="label">
              {file === null ? "Choose CSV" : file.name}
              <input
                hidden
                type="file"
                accept=".csv,text/csv"
                aria-label="Statement file"
                onChange={(e) => {
                  const chosen = e.target.files?.[0];
                  if (!chosen) return;
                  void chosen.text().then((text) => {
                    setFile({ name: chosen.name, text });
                  });
                }}
              />
            </Button>
            <Button variant="contained" disabled={!ready} onClick={() => void upload()}>
              Reconcile
            </Button>
          </Box>
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Statements" subheader="Each upload is kept as read; to match again, upload again." />
        <CardContent>
          {runs.result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {runs.result.state === "error" ? <Alert severity="error">{runs.result.message}</Alert> : null}
          {runs.result.state === "ready" && runs.result.data.length === 0 ? <Typography color="text.secondary">No statement yet.</Typography> : null}
          {runs.result.state === "ready" && runs.result.data.length > 0 ? (
            <Table size="small" aria-label="Statements">
              <TableHead>
                <TableRow>
                  <TableCell>Run</TableCell>
                  <TableCell>Provider</TableCell>
                  <TableCell>Period</TableCell>
                  <TableCell align="right">Lines</TableCell>
                  <TableCell align="right">In</TableCell>
                  <TableCell align="right">Out</TableCell>
                  <TableCell align="right">Fees</TableCell>
                  <TableCell>To look at</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {runs.result.data.map((r) => {
                  const look = r.lines - r.counts.matched;
                  return (
                    <TableRow
                      key={r.run}
                      hover
                      selected={open === r.run}
                      sx={{ cursor: "pointer" }}
                      onClick={() => {
                        setOpen(open === r.run ? null : r.run);
                      }}
                    >
                      <TableCell sx={{ fontFamily: "monospace" }}>{r.run}</TableCell>
                      <TableCell>{r.provider}</TableCell>
                      <TableCell>
                        {when(r.period_from).slice(0, 10)} to {when(r.period_to - 1).slice(0, 10)}
                      </TableCell>
                      <TableCell align="right">{r.lines}</TableCell>
                      <TableCell align="right">{usd(r.in_cents)}</TableCell>
                      <TableCell align="right">{usd(r.out_cents)}</TableCell>
                      <TableCell align="right">{usd(r.fee_cents)}</TableCell>
                      <TableCell>{look === 0 ? <Chip size="small" label="All matched" color="success" variant="outlined" /> : <Chip size="small" label={`${String(look)} lines`} color="error" />}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
      {open !== null ? (
        <Card>
          <CardHeader title={`Statement ${open}`} />
          <CardContent>
            <RunDetail run={open} />
          </CardContent>
        </Card>
      ) : null}
    </Stack>
  );
}
