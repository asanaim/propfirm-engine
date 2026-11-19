import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, FormControlLabel, MenuItem, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

interface Line {
  account: string;
  type: "asset" | "liability" | "equity" | "income" | "expense";
  name: string;
  balance_cents: number;
}

interface Expense {
  exp: string;
  category: string;
  amount_cents: number;
  memo: string;
  paid: boolean;
  status: "proposed" | "approved" | "rejected";
  proposed_by: string;
  decided_by: string | null;
}

export const CATEGORIES = ["hosting", "market_data", "software", "identity_checks", "processor_fees", "legal", "marketing", "staff", "other"];
const TYPES: Line["type"][] = ["asset", "liability", "equity", "income", "expense"];
const HEAD: Record<Line["type"], string> = { asset: "Assets", liability: "Liabilities", equity: "Equity", income: "Income", expense: "Expenses" };

/** Debit-positive balances shown the way an accountant reads them: assets
and expenses as debits, the others as credits (positive when normal). */
export function shown(line: Line): number {
  return line.type === "asset" || line.type === "expense" ? line.balance_cents : -line.balance_cents;
}

/** A dollar amount typed by staff, as whole cents; null if not a positive
amount with at most two decimals. */
export function cents(text: string): number | null {
  const m = /^(\d{1,10})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (m === null) return null;
  const value = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return value > 0 ? value : null;
}

type Row = Record<string, number> & { month: string; product: string };

export const LINES: Record<"accrual" | "cash", [string, string][]> = {
  accrual: [
    ["fees", "Fees"],
    ["discounts", "Discounts"],
    ["fee_refunds", "Fee refunds"],
    ["net_revenue", "Net revenue"],
    ["payouts", "Trader payouts"],
    ["commissions", "Commissions"],
    ["contribution", "Contribution"],
    ["operating", "Operating expenses"],
    ["profit", "Profit"],
  ],
  cash: [
    ["received", "Received"],
    ["payouts_paid", "Payouts paid"],
    ["commissions_paid", "Commissions paid"],
    ["expenses_paid", "Expenses paid"],
    ["net_cash", "Net cash"],
  ],
};

/** The month of `ms` as YYYY-MM, UTC (the statement's months). */
export function utcMonth(ms: number): string {
  return new Date(ms).toISOString().slice(0, 7);
}

/** Income statement (D-14.17): lines down, months across, one product
or all; accrual or cash view; CSV of every product. */
function Statement() {
  const { api } = useBackOffice();
  const [range, setRange] = useState(() => {
    const now = utcMonth(Date.now());
    return { from: `${now.slice(0, 4)}-01`, to: now }; // this year to date
  });
  const [view, setView] = useState<"accrual" | "cash">("accrual");
  const [product, setProduct] = useState("all");
  const q = `?from=${range.from}&to=${range.to}&view=${view}`;
  const { result } = useGet<{ rows: Row[] }>(`/staff/books/income-statement${q}`);
  const [error, setError] = useState<string | null>(null);
  const rows = result.state === "ready" ? result.data.rows : [];
  const products = [...new Set(rows.map((r) => r.product))].sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b)));
  const shownRows = rows.filter((r) => r.product === product);
  const save = async () => {
    if (api.text === undefined) return;
    setError(null);
    try {
      const csv = await api.text(`/staff/books/income-statement.csv${q}`);
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `income-${view}-${range.from}-${range.to}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The export failed.");
    }
  };
  return (
    <Card>
      <CardHeader title="Income statement" subheader="By calendar month (UTC). Accrual: when earned or owed. Cash: when paid." />
      <CardContent>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mb: 2, alignItems: "center" }}>
          <TextField label="From" type="month" size="small" value={range.from} onChange={(e) => { setRange({ ...range, from: e.target.value }); }} slotProps={{ inputLabel: { shrink: true } }} />
          <TextField label="To" type="month" size="small" value={range.to} onChange={(e) => { setRange({ ...range, to: e.target.value }); }} slotProps={{ inputLabel: { shrink: true } }} />
          <TextField select label="View" size="small" value={view} onChange={(e) => { setView(e.target.value as "accrual" | "cash"); }} sx={{ minWidth: 120 }}>
            <MenuItem value="accrual">Accrual</MenuItem>
            <MenuItem value="cash">Cash</MenuItem>
          </TextField>
          <TextField select label="Product" size="small" value={product} onChange={(e) => { setProduct(e.target.value); }} sx={{ minWidth: 140 }}>
            {(products.length > 0 ? products : ["all"]).map((p) => (
              <MenuItem key={p} value={p}>
                {p === "all" ? "All products" : p === "none" ? "No product" : p}
              </MenuItem>
            ))}
          </TextField>
          {api.text !== undefined ? (
            <Button variant="outlined" onClick={() => void save()}>
              Export CSV
            </Button>
          ) : null}
        </Stack>
        {error !== null ? <Alert severity="error">{error}</Alert> : null}
        {result.state === "error" ? <Alert severity="error">The statement could not be read.</Alert> : null}
        {result.state === "ready" ? (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell />
                  {shownRows.map((r) => (
                    <TableCell key={r.month} align="right">
                      {r.month}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {LINES[view].map(([key, label]) => {
                  const strong = key === "net_revenue" || key === "contribution" || key === "profit" || key === "net_cash";
                  return (
                    <TableRow key={key}>
                      <TableCell sx={{ fontWeight: strong ? 600 : 400 }}>{label}</TableCell>
                      {shownRows.map((r) => (
                        <TableCell key={r.month} align="right" sx={{ fontVariantNumeric: "tabular-nums", fontWeight: strong ? 600 : 400 }}>
                          {usd(r[key] ?? 0)}
                        </TableCell>
                      ))}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** The operator ledger (D-14.16): trial balance, and operating expenses
proposed by one member of staff and approved by another. */
export function BooksPage() {
  const { api } = useBackOffice();
  const tb = useGet<{ lines: Line[]; total_cents: number }>("/staff/books/trial-balance");
  const ex = useGet<Expense[]>("/staff/books/expenses");
  const [form, setForm] = useState({ category: "hosting", amount: "", memo: "", paid: true });
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      tb.reload();
      ex.reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  const amount = cents(form.amount);
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Books" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Statement />
      <Card>
        <CardHeader title="Trial balance" subheader="Posted from paid orders, payouts, commissions and approved expenses, once each." />
        <CardContent>
          {tb.result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {tb.result.state === "error" ? <Alert severity="error">The books could not be read.</Alert> : null}
          {tb.result.state === "ready" ? (
            <Table size="small">
              <TableBody>
                {TYPES.map((type) => {
                  const lines = tb.result.state === "ready" ? tb.result.data.lines.filter((x) => x.type === type) : [];
                  if (lines.length === 0) return null;
                  return [
                    <TableRow key={type}>
                      <TableCell colSpan={2} sx={{ fontWeight: 600, pt: 2 }}>
                        {HEAD[type]}
                      </TableCell>
                    </TableRow>,
                    ...lines.map((x) => (
                      <TableRow key={x.account}>
                        <TableCell>{x.name}</TableCell>
                        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                          {usd(shown(x))}
                        </TableCell>
                      </TableRow>
                    )),
                  ];
                })}
              </TableBody>
            </Table>
          ) : null}
          {tb.result.state === "ready" && tb.result.data.total_cents !== 0 ? <Alert severity="error">The book does not balance.</Alert> : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="New expense" subheader="Posted when a second member of staff approves it." />
        <CardContent>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ alignItems: "center" }}>
            <TextField
              select
              label="Category"
              size="small"
              value={form.category}
              onChange={(e) => {
                setForm({ ...form, category: e.target.value });
              }}
              sx={{ minWidth: 170 }}
            >
              {CATEGORIES.map((c) => (
                <MenuItem key={c} value={c}>
                  {c.replace("_", " ")}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Amount ($)"
              size="small"
              value={form.amount}
              onChange={(e) => {
                setForm({ ...form, amount: e.target.value });
              }}
              sx={{ width: 130 }}
            />
            <TextField
              label="Memo"
              size="small"
              value={form.memo}
              onChange={(e) => {
                setForm({ ...form, memo: e.target.value });
              }}
              sx={{ flexGrow: 1 }}
            />
            <FormControlLabel
              control={
                <Switch
                  checked={form.paid}
                  onChange={(e) => {
                    setForm({ ...form, paid: e.target.checked });
                  }}
                />
              }
              label={form.paid ? "Paid" : "Owed"}
            />
            <Button
              variant="contained"
              disabled={busy || amount === null || form.memo.trim() === ""}
              onClick={() => {
                if (amount === null) return;
                void run("/staff/books/expenses", { category: form.category, amount_cents: amount, memo: form.memo.trim(), paid: form.paid, incurred_at: Date.now() }, "Expense proposed.").then(() => {
                  setForm({ ...form, amount: "", memo: "" });
                });
              }}
            >
              Propose
            </Button>
          </Stack>
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Expenses" />
        <CardContent>
          <TextField
            label="Reason for a decision"
            value={why}
            onChange={(e) => {
              setWhy(e.target.value);
            }}
            size="small"
            fullWidth
            sx={{ mb: 1.5 }}
          />
          {ex.result.state === "ready" && ex.result.data.length === 0 ? <Typography color="text.secondary">No expenses yet.</Typography> : null}
          {ex.result.state === "ready" && ex.result.data.length > 0 ? (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Expense</TableCell>
                  <TableCell>Category</TableCell>
                  <TableCell>Memo</TableCell>
                  <TableCell align="right">Amount</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {ex.result.data.map((x) => (
                  <TableRow key={x.exp}>
                    <TableCell sx={{ fontFamily: "monospace" }}>{x.exp}</TableCell>
                    <TableCell>{x.category.replace("_", " ")}</TableCell>
                    <TableCell>{x.memo}</TableCell>
                    <TableCell align="right">{usd(x.amount_cents)}</TableCell>
                    <TableCell>
                      <Chip size="small" label={x.status} color={x.status === "approved" ? "success" : x.status === "rejected" ? "default" : "warning"} />
                    </TableCell>
                    <TableCell>
                      {x.status === "proposed" ? (
                        <Stack direction="row" spacing={1}>
                          <Button size="small" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/books/expenses/${x.exp}/decision`, { approve: true, reason: why.trim() }, `${x.exp} approved and posted.`)}>
                            Approve
                          </Button>
                          <Button size="small" color="inherit" disabled={busy || why.trim() === ""} onClick={() => void run(`/staff/books/expenses/${x.exp}/decision`, { approve: false, reason: why.trim() }, `${x.exp} rejected.`)}>
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
