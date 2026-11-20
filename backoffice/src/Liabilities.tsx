import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { cents } from "./Books";
import { useBackOffice } from "./context";
import { when } from "./format";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

interface Claim {
  acc: string;
  cif: string;
  product_id: string;
  certain_cents: number;
  open_cents: number;
  expected_cents: number;
  total_cents: number;
  open_profit_cents?: number;
}

/** Payout reserve coverage (D-14.20). */
export interface Reserve {
  reserve_cents: number;
  certain_cents: number;
  open_profit_cents: number;
  expected_cents: number;
  evaluations: number;
  largest_customer_cents: number;
  required_cents: number;
  k: string | null;
  state: "normal" | "amber" | "red" | "breach";
  releasable_cents: number;
  thresholds: { amber_below: string; red_below: string; breach_below: string; release_above: string };
  weights: { open: string; expected: string };
}

export interface Desk {
  as_of: number;
  certain_cents: number;
  payouts_certain_cents: number;
  commissions_cents: number;
  open_cents: number;
  expected_cents: number;
  total_cents: number;
  funded_accounts: number;
  largest: Claim | null;
  claims: Claim[];
  unread: string[];
  open_profit_cents?: number;
  largest_customer_cents?: number;
  reserve?: Reserve | null;
}

/** What each coverage state means and what follows it (D-14.20). */
export const STATES: Record<Reserve["state"], { label: string; color: "success" | "warning" | "error"; note: string }> = {
  normal: { label: "Normal", color: "success", note: "The reserve covers what is owed with room to spare." },
  amber: { label: "Amber", color: "warning", note: "In force: discount codes paused, scaling frozen, Instant capped at its 30 day daily average." },
  red: { label: "Red", color: "error", note: "In force: amber's limits, Instant sales stopped and new funded accounts queued. Owners: reprice the product with the highest payout ratio." },
  breach: { label: "Breach", color: "error", note: "In force: red's limits and a payout delay notice on the trader site. Owners: add capital below or stop the worst product." },
};

/** The parts of the requirement R*: label, amount, note. */
export function requirement(r: Reserve): [string, number, string][] {
  return [
    ["Owed on closed profit", r.certain_cents, "certain, open and expected claims"],
    [`Open profit x ${r.weights.open}`, r.open_profit_cents, "traders' share still in open positions"],
    [`Evaluations x ${r.weights.expected}`, r.expected_cents, `expected payouts of ${String(r.evaluations)} active evaluations`],
    ["Largest customer", r.largest_customer_cents, "the most one customer could claim now"],
  ];
}

/** The reserve panel: balance, requirement, coverage K and its state. */
function ReservePanel({ r }: { r: Reserve }) {
  const state = STATES[r.state];
  return (
    <Card>
      <CardHeader
        title="Payout reserve"
        subheader={`Coverage K = reserve / requirement. Amber below ${r.thresholds.amber_below}, red below ${r.thresholds.red_below}, breach below ${r.thresholds.breach_below}.`}
        action={<Chip label={`${state.label}${r.k === null ? "" : `, K ${r.k}`}`} color={state.color} sx={{ fontWeight: 600 }} />}
      />
      <CardContent>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" }, mb: 2 }}>
          {(
            [
              ["Reserve", r.reserve_cents, r.reserve_cents < 0 ? "drawn below zero: owed to the reserve" : "swept from sales, less payouts paid"],
              ["Requirement (R*)", r.required_cents, r.k === null ? "nothing owed" : `coverage K ${r.k}`],
              [`Above ${r.thresholds.release_above} x R*`, r.releasable_cents, "may return to operating cash monthly"],
            ] as [string, number, string][]
          ).map(([label, cents, note]) => (
            <Box key={label}>
              <Typography variant="overline" color="text.secondary">
                {label}
              </Typography>
              <Typography variant="h5" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {usd(cents)}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {note}
              </Typography>
            </Box>
          ))}
        </Box>
        <Alert severity={r.state === "normal" ? "success" : r.state === "amber" ? "warning" : "error"} sx={{ mb: 2 }}>
          {state.note}
        </Alert>
        <Table size="small" aria-label="Requirement">
          <TableBody>
            {requirement(r).map(([label, cents, note]) => (
              <TableRow key={label}>
                <TableCell>{label}</TableCell>
                <TableCell sx={{ color: "text.secondary" }}>{note}</TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {usd(cents)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

/** The tiles of the desk: label, amount, note. */
export function tiles(d: Desk): [string, number, string][] {
  return [
    ["Certain", d.certain_cents, `payouts ${usd(d.payouts_certain_cents)}, commissions ${usd(d.commissions_cents)}`],
    ["Open", d.open_cents, "payouts requested, not yet decided"],
    ["Expected", d.expected_cents, `if all ${String(d.funded_accounts)} funded accounts asked now`],
    ["Total", d.total_cents, d.largest ? `largest single claim ${usd(d.largest.total_cents)} (${d.largest.acc})` : "no claims"],
  ];
}

/** Liability desk (D-14.18): what the firm owes or may owe traders. */
interface Move {
  rsm: string;
  kind: "topup" | "release";
  amount_cents: number;
  reason: string;
  proposed_by: string;
  proposed_at: number;
  status: "proposed" | "approved" | "rejected";
  decided_by: string | null;
}

/** Owners' capital into the reserve, and surplus back to cash: proposed by
one member of staff, decided by another (D-14.23). */
function ReserveMoves() {
  const { api } = useBackOffice();
  const moves = useGet<Move[]>("/staff/reserve/moves");
  const [form, setForm] = useState({ kind: "topup", amount: "", reason: "" });
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const amount = cents(form.amount);
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      moves.reload();
      return true;
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader title="Top-ups and releases" subheader="Owners' capital into the reserve, or what lies above the release line back to cash. Someone else decides; the books post it within a minute." />
      <CardContent>
        <Stack spacing={1.5}>
          {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <TextField select size="small" label="Move" value={form.kind} onChange={(e) => { setForm({ ...form, kind: e.target.value }); }} sx={{ minWidth: 150 }}>
              <MenuItem value="topup">Top-up</MenuItem>
              <MenuItem value="release">Release</MenuItem>
            </TextField>
            <TextField size="small" label="Amount (USD)" value={form.amount} onChange={(e) => { setForm({ ...form, amount: e.target.value }); }} sx={{ width: 150 }} />
            <TextField size="small" label="Reason" value={form.reason} onChange={(e) => { setForm({ ...form, reason: e.target.value }); }} sx={{ flexGrow: 1 }} />
            <Button
              variant="contained"
              disabled={busy || amount === null || form.reason.trim() === ""}
              onClick={() => {
                if (amount === null) return;
                void run("/staff/reserve/moves", { kind: form.kind, amount_cents: amount, reason: form.reason.trim() }, "Proposed.").then((ok) => {
                  if (ok) setForm({ ...form, amount: "", reason: "" });
                });
              }}
            >
              Propose
            </Button>
          </Stack>
          <TextField size="small" label="Reason for a decision" value={why} onChange={(e) => { setWhy(e.target.value); }} />
          {moves.result.state === "ready" && moves.result.data.length > 0 ? (
            <Table size="small" aria-label="Reserve moves">
              <TableHead>
                <TableRow>
                  <TableCell>Move</TableCell>
                  <TableCell>Kind</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Proposed</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {moves.result.data.map((m) => (
                  <TableRow key={m.rsm}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{m.rsm}</TableCell>
                    <TableCell>{m.kind === "topup" ? "Top-up" : "Release"}</TableCell>
                    <TableCell align="right">{usd(m.amount_cents)}</TableCell>
                    <TableCell>{m.reason}</TableCell>
                    <TableCell>{when(m.proposed_at)} by {m.proposed_by}</TableCell>
                    <TableCell>
                      <Chip size="small" label={m.status} color={m.status === "approved" ? "success" : m.status === "proposed" ? "warning" : "default"} />
                    </TableCell>
                    <TableCell>
                      {m.status === "proposed" ? (
                        <Stack direction="row" spacing={1}>
                          <Button size="small" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/reserve/moves/${m.rsm}/decision`, { approve: true, reason: why.trim() }, `${m.rsm} approved.`)}>
                            Approve
                          </Button>
                          <Button size="small" color="inherit" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/reserve/moves/${m.rsm}/decision`, { approve: false, reason: why.trim() }, `${m.rsm} rejected.`)}>
                            Reject
                          </Button>
                        </Stack>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

export function LiabilitiesPage() {
  const { result } = useGet<Desk>("/staff/liabilities");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Liabilities" />
      {result.state === "loading" ? <Typography color="text.secondary">Reading every funded account…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">The desk could not be read.</Alert> : null}
      {result.state === "ready" ? (
        <>
          {result.data.unread.length > 0 ? <Alert severity="warning">Not read, so not counted: {result.data.unread.join(", ")}</Alert> : null}
          <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", lg: "repeat(4, 1fr)" } }}>
            {tiles(result.data).map(([label, cents, note]) => (
              <Card key={label}>
                <CardContent>
                  <Typography variant="overline" color="text.secondary">
                    {label}
                  </Typography>
                  <Typography variant="h5" sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {usd(cents)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {note}
                  </Typography>
                </CardContent>
              </Card>
            ))}
          </Box>
          {result.data.reserve ? <ReservePanel r={result.data.reserve} /> : null}
          {result.data.reserve ? <ReserveMoves /> : null}
          <Card>
            <CardHeader title="Largest claims" subheader={`As of ${new Date(result.data.as_of).toLocaleString()}`} />
            <CardContent>
              {result.data.claims.length === 0 ? (
                <Typography color="text.secondary">No claims.</Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Account</TableCell>
                      <TableCell>Customer</TableCell>
                      <TableCell>Product</TableCell>
                      <TableCell align="right">Certain</TableCell>
                      <TableCell align="right">Open</TableCell>
                      <TableCell align="right">Expected</TableCell>
                      <TableCell align="right">Total</TableCell>
                      <TableCell align="right">Open profit</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.data.claims.map((c) => (
                      <TableRow key={c.acc}>
                        <TableCell sx={{ fontFamily: "monospace" }}>{c.acc}</TableCell>
                        <TableCell sx={{ fontFamily: "monospace" }}>{c.cif}</TableCell>
                        <TableCell>{c.product_id}</TableCell>
                        <TableCell align="right">{usd(c.certain_cents)}</TableCell>
                        <TableCell align="right">{usd(c.open_cents)}</TableCell>
                        <TableCell align="right">{usd(c.expected_cents)}</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 600 }}>
                          {usd(c.total_cents)}
                        </TableCell>
                        <TableCell align="right" sx={{ color: "text.secondary" }}>
                          {usd(c.open_profit_cents ?? 0)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </Stack>
  );
}
