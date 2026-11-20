import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { cents } from "./Books";
import { useBackOffice } from "./context";
import { when } from "./format";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

interface Refund {
  rfd: string;
  ord: string;
  cif: string;
  product_id: string;
  price_cents: number;
  amount_cents: number;
  reason: string;
  proposed_by: string;
  proposed_at: number;
  status: "proposed" | "approved" | "rejected";
  decided_by: string | null;
  ended_acc: string | null;
}

interface Chargeback {
  cbk: string;
  ord: string;
  cif: string;
  product_id: string;
  amount_cents: number;
  fee_cents: number;
  provider_case: string;
  reason: string;
  received_at: number;
  recorded_by: string;
  status: "open" | "won" | "lost";
  cas: string | null;
  ended_acc: string | null;
}

/** An order id as typed: ORD and 8 digits; null if not one. */
export function orderId(text: string): string | null {
  const ref = text.trim().toUpperCase();
  return /^ORD\d{8}$/.test(ref) ? ref : null;
}

/** A fee in dollars: 0 or more, at most two decimals; null if not one. */
export function feeCents(text: string): number | null {
  const m = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (text.trim() === "") return 0;
  if (m === null) return null;
  return Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
}

const STATE_COLOR: Record<string, "success" | "warning" | "error" | "default"> = {
  proposed: "warning",
  approved: "success",
  rejected: "default",
  open: "error",
  won: "success",
  lost: "default",
};

/** Refunds and chargebacks (D-14.22): a refund is proposed by one member of
staff and decided by another; a chargeback ends the account, holds payouts
by a case and stops purchases at once, and another member of staff records
its outcome. */
export function DisputesPage() {
  const { api } = useBackOffice();
  const refunds = useGet<Refund[]>("/staff/refunds");
  const charges = useGet<Chargeback[]>("/staff/chargebacks");
  const [refund, setRefund] = useState({ ord: "", amount: "", reason: "" });
  const [charge, setCharge] = useState({ ord: "", amount: "", fee: "", case: "", reason: "", day: "" });
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      refunds.reload();
      charges.reload();
      return true;
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const refundOrd = orderId(refund.ord);
  const refundAmount = cents(refund.amount);
  const chargeOrd = orderId(charge.ord);
  const chargeAmount = cents(charge.amount);
  const chargeFee = feeCents(charge.fee);
  const received = /^\d{4}-\d{2}-\d{2}$/.test(charge.day) ? Date.parse(`${charge.day}T00:00:00Z`) : null;
  const reason = why.trim();
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Refunds and chargebacks" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Box sx={{ display: "grid", gap: 2.5, gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" } }}>
        <Card>
          <CardHeader title="Propose a refund" subheader="A refund in full ends the account and takes back any referral commission. Someone else decides it." />
          <CardContent>
            <Stack spacing={1.5}>
              <TextField label="Order" size="small" placeholder="ORD00000001" value={refund.ord} onChange={(e) => { setRefund({ ...refund, ord: e.target.value }); }} />
              <TextField label="Amount (USD)" size="small" value={refund.amount} onChange={(e) => { setRefund({ ...refund, amount: e.target.value }); }} />
              <TextField label="Reason" size="small" value={refund.reason} onChange={(e) => { setRefund({ ...refund, reason: e.target.value }); }} />
              <Box>
                <Button
                  variant="contained"
                  disabled={busy || refundOrd === null || refundAmount === null || refund.reason.trim() === ""}
                  onClick={() => {
                    if (refundOrd === null || refundAmount === null) return;
                    void run(`/staff/orders/${refundOrd}/refunds`, { amount_cents: refundAmount, reason: refund.reason.trim() }, "Refund proposed.").then((ok) => {
                      if (ok) setRefund({ ord: "", amount: "", reason: "" });
                    });
                  }}
                >
                  Propose
                </Button>
              </Box>
            </Stack>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Record a chargeback" subheader="At once: the account ends, a case holds payouts and the customer cannot buy until it is won." />
          <CardContent>
            <Stack spacing={1.5}>
              <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: "1fr 1fr" }}>
                <TextField label="Order" size="small" placeholder="ORD00000001" value={charge.ord} onChange={(e) => { setCharge({ ...charge, ord: e.target.value }); }} />
                <TextField label="Processor case" size="small" value={charge.case} onChange={(e) => { setCharge({ ...charge, case: e.target.value }); }} />
                <TextField label="Amount (USD)" size="small" value={charge.amount} onChange={(e) => { setCharge({ ...charge, amount: e.target.value }); }} />
                <TextField label="Processor fee (USD)" size="small" value={charge.fee} onChange={(e) => { setCharge({ ...charge, fee: e.target.value }); }} />
                <TextField label="Taken on (UTC)" type="date" size="small" value={charge.day} slotProps={{ inputLabel: { shrink: true } }} onChange={(e) => { setCharge({ ...charge, day: e.target.value }); }} />
                <TextField label="Reason" size="small" value={charge.reason} onChange={(e) => { setCharge({ ...charge, reason: e.target.value }); }} />
              </Box>
              <Box>
                <Button
                  variant="contained"
                  color="error"
                  disabled={busy || chargeOrd === null || chargeAmount === null || chargeFee === null || received === null || charge.case.trim() === "" || charge.reason.trim() === ""}
                  onClick={() => {
                    if (chargeOrd === null || chargeAmount === null || chargeFee === null || received === null) return;
                    void run(
                      `/staff/orders/${chargeOrd}/chargebacks`,
                      { amount_cents: chargeAmount, fee_cents: chargeFee, provider_case: charge.case.trim(), reason: charge.reason.trim(), received_at: received },
                      "Chargeback recorded: the account is ended and payouts are held.",
                    ).then((ok) => {
                      if (ok) setCharge({ ord: "", amount: "", fee: "", case: "", reason: "", day: "" });
                    });
                  }}
                >
                  Record
                </Button>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      </Box>
      <TextField label="Reason for a decision" value={why} onChange={(e) => { setWhy(e.target.value); }} size="small" fullWidth />
      <Card>
        <CardHeader title="Refunds" />
        <CardContent>
          {refunds.result.state === "error" ? <Alert severity="error">{refunds.result.message}</Alert> : null}
          {refunds.result.state === "ready" && refunds.result.data.length === 0 ? <Typography color="text.secondary">No refunds.</Typography> : null}
          {refunds.result.state === "ready" && refunds.result.data.length > 0 ? (
            <Table size="small" aria-label="Refunds">
              <TableHead>
                <TableRow>
                  <TableCell>Refund</TableCell>
                  <TableCell>Order</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {refunds.result.data.map((r) => (
                  <TableRow key={r.rfd}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{r.rfd}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{r.ord}</TableCell>
                    <TableCell align="right">
                      {usd(r.amount_cents)} <Typography component="span" variant="caption" color="text.secondary">of {usd(r.price_cents)}</Typography>
                    </TableCell>
                    <TableCell>{r.reason}</TableCell>
                    <TableCell>
                      <Chip size="small" label={r.ended_acc ? `${r.status}, ${r.ended_acc} ended` : r.status} color={STATE_COLOR[r.status]} />
                    </TableCell>
                    <TableCell>
                      {r.status === "proposed" ? (
                        <Stack direction="row" spacing={1}>
                          <Button size="small" disabled={busy || reason === ""} onClick={() => void run(`/staff/refunds/${r.rfd}/decision`, { approve: true, reason }, `${r.rfd} approved.`)}>
                            Approve
                          </Button>
                          <Button size="small" color="inherit" disabled={busy || reason === ""} onClick={() => void run(`/staff/refunds/${r.rfd}/decision`, { approve: false, reason }, `${r.rfd} rejected.`)}>
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
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Chargebacks" />
        <CardContent>
          {charges.result.state === "error" ? <Alert severity="error">{charges.result.message}</Alert> : null}
          {charges.result.state === "ready" && charges.result.data.length === 0 ? <Typography color="text.secondary">No chargebacks.</Typography> : null}
          {charges.result.state === "ready" && charges.result.data.length > 0 ? (
            <Table size="small" aria-label="Chargebacks">
              <TableHead>
                <TableRow>
                  <TableCell>Chargeback</TableCell>
                  <TableCell>Order</TableCell>
                  <TableCell>Taken on</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell align="right">Fee</TableCell>
                  <TableCell>Case</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {charges.result.data.map((c) => (
                  <TableRow key={c.cbk}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{c.cbk}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{c.ord}</TableCell>
                    <TableCell>{when(c.received_at).slice(0, 10)}</TableCell>
                    <TableCell align="right">{usd(c.amount_cents)}</TableCell>
                    <TableCell align="right">{usd(c.fee_cents)}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{c.cas ?? "-"}</TableCell>
                    <TableCell>
                      <Chip size="small" label={c.status} color={STATE_COLOR[c.status]} />
                    </TableCell>
                    <TableCell>
                      {c.status === "open" ? (
                        <Stack direction="row" spacing={1}>
                          <Button size="small" disabled={busy || reason === ""} onClick={() => void run(`/staff/chargebacks/${c.cbk}/decision`, { outcome: "won", reason }, `${c.cbk} won: purchases allowed again.`)}>
                            Won
                          </Button>
                          <Button size="small" color="inherit" disabled={busy || reason === ""} onClick={() => void run(`/staff/chargebacks/${c.cbk}/decision`, { outcome: "lost", reason }, `${c.cbk} lost.`)}>
                            Lost
                          </Button>
                        </Stack>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </Stack>
  );
}
