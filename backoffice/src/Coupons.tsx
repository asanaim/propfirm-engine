import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

export interface Code {
  code: string;
  discount_percent: string;
  on: boolean;
  valid: boolean;
  created_at: number;
  created_by: string | null;
  expires_at: number | null;
  max_uses: number | null;
  uses: number;
  paid: number;
  given_cents: number;
}

/** What to send for a new code; null fields left out. `ends` is a local
date (YYYY-MM-DD): the code ends at the start of that day, local time. */
export function newCode(code: string, percent: string, ends: string, maxUses: string, reason: string) {
  return {
    code: code.trim().toUpperCase(),
    discount_percent: percent.trim(),
    ...(ends === "" ? {} : { expires_at: new Date(`${ends}T00:00:00`).getTime() }),
    ...(maxUses.trim() === "" ? {} : { max_uses: Number(maxUses) }),
    reason: reason.trim(),
  };
}

function state(c: Code): { label: string; color: "success" | "default" | "warning" } {
  if (!c.on) return { label: "Off", color: "default" };
  if (!c.valid) return { label: c.max_uses !== null && c.uses >= c.max_uses ? "Used up" : "Ended", color: "warning" };
  return { label: "Valid", color: "success" };
}

/** Discount codes (D-14.14): create, switch, and see uses and what each
gave away. A code takes off at most the product's cap and only on a
first account. */
export function CouponsPage() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<Code[]>("/staff/codes");
  const [form, setForm] = useState({ code: "", percent: "10", ends: "", maxUses: "", reason: "" });
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  const ready = /^[A-Za-z0-9-]{3,32}$/.test(form.code.trim()) && form.percent.trim() !== "" && form.reason.trim() !== "";
  const field = (key: keyof typeof form) => (e: { target: { value: string } }) => {
    setForm({ ...form, [key]: e.target.value });
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Coupons" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="New code" subheader="Valid on a first account only, up to each product's cap (10 %; none on Instant or Lite 10K)." />
        <CardContent>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <TextField label="Code" value={form.code} onChange={field("code")} size="small" />
            <TextField label="Discount %" value={form.percent} onChange={field("percent")} size="small" sx={{ width: 120 }} />
            <TextField label="Ends" type="date" value={form.ends} onChange={field("ends")} size="small" slotProps={{ inputLabel: { shrink: true } }} />
            <TextField label="Max uses" value={form.maxUses} onChange={field("maxUses")} size="small" sx={{ width: 120 }} />
            <TextField label="Reason" value={form.reason} onChange={field("reason")} size="small" sx={{ flexGrow: 1 }} />
            <Button
              variant="contained"
              disabled={busy || !ready}
              onClick={() => {
                void run("/staff/codes", newCode(form.code, form.percent, form.ends, form.maxUses, form.reason), "Code created.").then(() => {
                  setForm({ code: "", percent: "10", ends: "", maxUses: "", reason: "" });
                });
              }}
            >
              Create
            </Button>
          </Stack>
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Codes" />
        <CardContent>
          <TextField label="Reason for a switch" value={why} onChange={(e) => { setWhy(e.target.value); }} size="small" fullWidth sx={{ mb: 1.5 }} helperText="Required to switch a code on or off; kept with the change." />
          {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {result.state === "error" ? <Alert severity="error">The codes could not be read.</Alert> : null}
          {result.state === "ready" && result.data.length === 0 ? <Typography color="text.secondary">No codes yet.</Typography> : null}
          {result.state === "ready" && result.data.length > 0 ? (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Code</TableCell>
                  <TableCell align="right">Off</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell align="right">Uses</TableCell>
                  <TableCell align="right">Paid</TableCell>
                  <TableCell align="right">Given away</TableCell>
                  <TableCell>Ends</TableCell>
                  <TableCell>On</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {result.data.map((c) => {
                  const s = state(c);
                  return (
                    <TableRow key={c.code}>
                      <TableCell sx={{ fontFamily: "monospace" }}>{c.code}</TableCell>
                      <TableCell align="right">{c.discount_percent} %</TableCell>
                      <TableCell>
                        <Chip size="small" label={s.label} color={s.color} />
                      </TableCell>
                      <TableCell align="right">{c.max_uses === null ? c.uses : `${String(c.uses)} / ${String(c.max_uses)}`}</TableCell>
                      <TableCell align="right">{c.paid}</TableCell>
                      <TableCell align="right">{usd(c.given_cents)}</TableCell>
                      <TableCell>{c.expires_at === null ? "No end" : new Date(c.expires_at).toLocaleDateString()}</TableCell>
                      <TableCell>
                        <Switch
                          checked={c.on}
                          disabled={busy || why.trim() === ""}
                          slotProps={{ input: { "aria-label": `${c.code} on` } }}
                          onChange={(e) => {
                            const on = e.target.checked;
                            void run(`/staff/codes/${c.code}/switch`, { on, reason: why.trim() }, on ? `${c.code} is on.` : `${c.code} is off.`).then(() => {
                              setWhy("");
                            });
                          }}
                        />
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
