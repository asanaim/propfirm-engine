import { Alert, Box, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
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
  amber: { label: "Amber", color: "warning", note: "Thin cover: hold promotions below list and scaling." },
  red: { label: "Red", color: "error", note: "New funded accounts should queue; owners act." },
  breach: { label: "Breach", color: "error", note: "Owners add capital or stop the worst product; publish any delay." },
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
