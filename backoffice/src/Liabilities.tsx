import { Alert, Box, Card, CardContent, CardHeader, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Title } from "react-admin";
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
