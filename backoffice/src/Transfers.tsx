import { Alert, Button, Card, CardContent, CardHeader, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

interface Waiting {
  po: string;
  acc: string;
  cif: string;
  method_id: string;
  trader_amount_cents: number;
  charge_cents: number;
  net_cents: number;
  currency: string;
  network: string;
  address: string;
}

/** Payouts to send by hand (D-14.25): approved and debited payouts of a
manual withdrawal method. Send the net amount from the firm's wallet, then
record the transaction; the payout is paid at the next round. */
export function TransfersPage() {
  const { api } = useBackOffice();
  const waiting = useGet<Waiting[]>("/staff/transfers");
  const [tx, setTx] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const record = async (po: string, ref: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/staff/payouts/${po}/transfer`, { tx_ref: ref });
      setMessage({ ok: true, text: `${po}: transfer recorded; it shows as paid within a minute.` });
      waiting.reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Payouts to send" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="Payouts to send" subheader="Send exactly the amount shown to the address shown, on its network, then record the transaction id." />
        <CardContent>
          {waiting.result.state === "error" ? <Alert severity="error">{waiting.result.message}</Alert> : null}
          {waiting.result.state === "ready" && waiting.result.data.length === 0 ? <Typography color="text.secondary">Nothing to send.</Typography> : null}
          {waiting.result.state === "ready" && waiting.result.data.length > 0 ? (
            <Table size="small" aria-label="Payouts to send">
              <TableHead>
                <TableRow>
                  <TableCell>Payout</TableCell>
                  <TableCell align="right">Send</TableCell>
                  <TableCell>To</TableCell>
                  <TableCell>Transaction</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {waiting.result.data.map((w) => {
                  const ref = (tx[w.po] ?? "").trim();
                  return (
                    <TableRow key={w.po}>
                      <TableCell sx={{ fontFamily: "monospace" }}>
                        {w.po}
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                          {w.acc}, {w.method_id}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <strong>
                          {usd(w.net_cents)} {w.currency}
                        </strong>
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                          {usd(w.trader_amount_cents)} less {usd(w.charge_cents)} charge
                        </Typography>
                      </TableCell>
                      <TableCell sx={{ fontFamily: "monospace" }}>
                        {w.address}
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                          {w.network}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <TextField
                          size="small"
                          label={`Transaction for ${w.po}`}
                          value={tx[w.po] ?? ""}
                          onChange={(e) => {
                            setTx({ ...tx, [w.po]: e.target.value });
                          }}
                        />
                      </TableCell>
                      <TableCell>
                        <Button size="small" variant="contained" disabled={busy || ref === ""} onClick={() => void record(w.po, ref)}>
                          Record
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
    </Stack>
  );
}
