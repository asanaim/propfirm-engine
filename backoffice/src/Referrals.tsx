import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

export interface Entry {
  id: number;
  referrer_cif: string;
  referred_cif: string;
  ord: string;
  kind: "accrued" | "clawback";
  amount_cents: number;
  available_at: number;
  by_login: string | null;
  reason: string | null;
  ts: number;
}

/** Orders whose commission can still be taken back. */
export function clawable(entries: Entry[]): Set<string> {
  const taken = new Set(entries.filter((e) => e.kind === "clawback").map((e) => e.ord));
  return new Set(entries.filter((e) => e.kind === "accrued" && !taken.has(e.ord)).map((e) => e.ord));
}

/** The referral ledger (D-14.15): 10 % of a referred trader's first paid
account, available after the holdback; a clawback is a new entry. */
export function ReferralsPage() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<Entry[]>("/staff/referrals");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const back = async (ord: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/staff/referrals/${ord}/clawback`, { reason: why.trim() });
      setMessage({ ok: true, text: `Commission on ${ord} taken back.` });
      setWhy("");
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  const open = result.state === "ready" ? clawable(result.data) : new Set<string>();
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Referrals" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="Referral ledger" subheader="Commission accrues on a referred trader's first paid account; it is paid out with the affiliate programme." />
        <CardContent>
          <TextField
            label="Reason for a clawback"
            value={why}
            onChange={(e) => {
              setWhy(e.target.value);
            }}
            size="small"
            fullWidth
            sx={{ mb: 1.5 }}
            helperText="Refund, self-referral or fraud; kept with the entry."
          />
          {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {result.state === "error" ? <Alert severity="error">The ledger could not be read.</Alert> : null}
          {result.state === "ready" && result.data.length === 0 ? <Typography color="text.secondary">No entries yet.</Typography> : null}
          {result.state === "ready" && result.data.length > 0 ? (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Order</TableCell>
                  <TableCell>Referrer</TableCell>
                  <TableCell>Referred</TableCell>
                  <TableCell>Entry</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>Available</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {result.data.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{e.ord}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{e.referrer_cif}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>{e.referred_cif}</TableCell>
                    <TableCell>
                      <Chip size="small" label={e.kind === "accrued" ? "Accrued" : `Clawback: ${e.reason ?? ""}`} color={e.kind === "accrued" ? "success" : "warning"} />
                    </TableCell>
                    <TableCell align="right">{usd(e.amount_cents)}</TableCell>
                    <TableCell>{new Date(e.available_at).toLocaleDateString()}</TableCell>
                    <TableCell>
                      {open.has(e.ord) && e.kind === "accrued" ? (
                        <Button
                          size="small"
                          disabled={busy || why.trim() === ""}
                          onClick={() => {
                            void back(e.ord);
                          }}
                        >
                          Claw back
                        </Button>
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
