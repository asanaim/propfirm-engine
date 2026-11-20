import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

interface Awaiting {
  ord: string;
  cif: string;
  provider: string;
  price_cents: number;
  created_at: number;
}

interface Receipt {
  rcp: string;
  ord: string;
  cif: string;
  provider: string;
  amount_cents: number;
  reference: string;
  received_at: number;
  recorded_by: string;
  status: "recorded" | "confirmed" | "rejected";
  decided_by: string | null;
}

interface Listing {
  awaiting: Awaiting[];
  receipts: Receipt[];
}

/** Today as a UTC day, YYYY-MM-DD. */
export function todayUtc(now: number): string {
  return new Date(now).toISOString().slice(0, 10);
}

/** Payments by hand (D-14.24): orders placed with a manual method (a bank
transfer) wait here. One member of staff records the money seen, with the
bank's reference; another confirms it, and the account opens. */
export function ReceiptsPage() {
  const { api } = useBackOffice();
  const listing = useGet<Listing>("/staff/receipts");
  const [refs, setRefs] = useState<Record<string, string>>({});
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [today] = useState(() => todayUtc(Date.now()));
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      listing.reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Manual receipts" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      {listing.result.state === "error" ? <Alert severity="error">{listing.result.message}</Alert> : null}
      <Card>
        <CardHeader title="Waiting for payment" subheader="Record a payment only once it is on the bank statement, for the order's exact price. The reference is the order number the customer quoted." />
        <CardContent>
          {listing.result.state === "ready" && listing.result.data.awaiting.length === 0 ? <Typography color="text.secondary">Nothing is waiting.</Typography> : null}
          {listing.result.state === "ready" && listing.result.data.awaiting.length > 0 ? (
            <Table size="small" aria-label="Waiting for payment">
              <TableHead>
                <TableRow>
                  <TableCell>Order</TableCell>
                  <TableCell>Method</TableCell>
                  <TableCell>Placed</TableCell>
                  <TableCell align="right">Price</TableCell>
                  <TableCell>Bank reference</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {listing.result.data.awaiting.map((o) => {
                  const ref = (refs[o.ord] ?? "").trim();
                  return (
                    <TableRow key={o.ord}>
                      <TableCell sx={{ fontFamily: "monospace" }}>{o.ord}</TableCell>
                      <TableCell>{o.provider}</TableCell>
                      <TableCell>{when(o.created_at)}</TableCell>
                      <TableCell align="right">{usd(o.price_cents)}</TableCell>
                      <TableCell>
                        <TextField
                          size="small"
                          label={`Reference for ${o.ord}`}
                          value={refs[o.ord] ?? ""}
                          onChange={(e) => {
                            setRefs({ ...refs, [o.ord]: e.target.value });
                          }}
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          size="small"
                          variant="contained"
                          disabled={busy || ref === ""}
                          onClick={() => void run(`/staff/orders/${o.ord}/receipts`, { amount_cents: o.price_cents, reference: ref, received_at: Date.parse(`${today}T00:00:00Z`) }, `Payment for ${o.ord} recorded; someone else confirms it.`)}
                        >
                          Record {usd(o.price_cents)}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
      <TextField size="small" label="Reason for a decision" value={why} onChange={(e) => { setWhy(e.target.value); }} />
      <Card>
        <CardHeader title="Recorded payments" subheader="Confirming one marks its order paid and opens the account." />
        <CardContent>
          {listing.result.state === "ready" && listing.result.data.receipts.length === 0 ? <Typography color="text.secondary">None yet.</Typography> : null}
          {listing.result.state === "ready" && listing.result.data.receipts.length > 0 ? (
            <Table size="small" aria-label="Recorded payments">
              <TableHead>
                <TableRow>
                  <TableCell>Receipt</TableCell>
                  <TableCell>Order</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>Reference</TableCell>
                  <TableCell>Recorded by</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {listing.result.data.receipts.map((r) => (
                  <TableRow key={r.rcp}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{r.rcp}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{r.ord}</TableCell>
                    <TableCell align="right">{usd(r.amount_cents)}</TableCell>
                    <TableCell>{r.reference}</TableCell>
                    <TableCell>{r.recorded_by}</TableCell>
                    <TableCell>
                      <Chip size="small" label={r.status} color={r.status === "confirmed" ? "success" : r.status === "recorded" ? "warning" : "default"} />
                    </TableCell>
                    <TableCell>
                      {r.status === "recorded" ? (
                        <Stack direction="row" spacing={1}>
                          <Button size="small" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/receipts/${r.rcp}/decision`, { confirm: true, reason: why.trim() }, `${r.rcp} confirmed: ${r.ord} is paid.`)}>
                            Confirm
                          </Button>
                          <Button size="small" color="inherit" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/receipts/${r.rcp}/decision`, { confirm: false, reason: why.trim() }, `${r.rcp} rejected.`)}>
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
    </Stack>
  );
}
