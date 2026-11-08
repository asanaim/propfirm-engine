import { Alert, Button, Card, CardContent, CardHeader, Chip, MenuItem, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, FormControlLabel, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

interface UserField {
  name: string;
  label: string;
  type: "text" | "number" | "email";
  required: boolean;
}
export interface Method {
  method_id: string;
  kind: "payment" | "withdrawal";
  name: string;
  adapter: string;
  enabled: boolean;
  min_cents: number;
  max_cents: number;
  fixed_charge_cents: number;
  percent_charge: string;
  instructions: string;
  user_fields: UserField[];
  secrets: string[];
}
interface MethodsPage {
  adapters: string[];
  methods: Method[];
}

const ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const NAME = /^[a-z][a-z0-9_]{0,39}$/;

/** Dollars typed as text to whole cents; null when not a valid amount. */
export function toCents(text: string): number | null {
  const m = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (m === null) return null;
  return Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
}

function dollars(cents: number): string {
  return (cents / 100).toFixed(2).replace(/\.00$/, "");
}

function useMessage() {
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const shown = message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>;
  return { setMessage, shown };
}

function MethodForm({ page, start, onDone }: { page: MethodsPage; start: Method | null; onDone: () => void }) {
  const { api } = useBackOffice();
  const [id, setId] = useState(start?.method_id ?? "");
  const [kind, setKind] = useState<Method["kind"]>(start?.kind ?? "payment");
  const [name, setName] = useState(start?.name ?? "");
  const [adapter, setAdapter] = useState(start?.adapter ?? page.adapters[0] ?? "");
  const [enabled, setEnabled] = useState(start?.enabled ?? false);
  const [min, setMin] = useState(start === null ? "0" : dollars(start.min_cents));
  const [max, setMax] = useState(start === null ? "0" : dollars(start.max_cents));
  const [fixed, setFixed] = useState(start === null ? "0" : dollars(start.fixed_charge_cents));
  const [percent, setPercent] = useState(start?.percent_charge ?? "0");
  const [instructions, setInstructions] = useState(start?.instructions ?? "");
  const [fields, setFields] = useState<UserField[]>(start?.user_fields ?? []);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const { setMessage, shown } = useMessage();
  const cents = [toCents(min), toCents(max), toCents(fixed)];
  const valid = ID.test(id) && name.trim() !== "" && reason.trim() !== "" && cents.every((c) => c !== null) && fields.every((f) => NAME.test(f.name) && f.label.trim() !== "");

  const propose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/admin/payment-methods/${id}`, {
        kind, name, adapter, enabled,
        min_cents: cents[0], max_cents: cents[1], fixed_charge_cents: cents[2],
        percent_charge: percent, instructions, user_fields: fields, reason,
      });
      setMessage({ ok: true, text: "Proposed. An owner who did not propose it approves it under Setting proposals." });
      setReason("");
      onDone();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };

  const set = (f: (v: string) => void) => (e: { target: { value: string } }) => {
    f(e.target.value);
  };
  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title={start === null ? "Attach a method" : `Change ${start.name}`} subheader="Enable or disable a method here too. Amounts in US dollars; 0 as maximum means none." />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField label="Method id (e.g. bank-transfer)" value={id} onChange={set((v) => { setId(v.trim().toLowerCase()); })} disabled={start !== null} error={id !== "" && !ID.test(id)} fullWidth />
            <TextField select label="Kind" value={kind} onChange={set((v) => { setKind(v as Method["kind"]); })} fullWidth>
              <MenuItem value="payment">Payment (buying a challenge)</MenuItem>
              <MenuItem value="withdrawal">Withdrawal (a payout)</MenuItem>
            </TextField>
          </Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField label="Name traders see" value={name} onChange={set(setName)} fullWidth />
            <TextField select label="Provider" value={adapter} onChange={set(setAdapter)} fullWidth>
              {page.adapters.map((a) => (
                <MenuItem key={a} value={a}>{a}</MenuItem>
              ))}
            </TextField>
          </Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField label="Minimum ($)" value={min} onChange={set(setMin)} error={cents[0] === null} fullWidth />
            <TextField label="Maximum ($, 0: none)" value={max} onChange={set(setMax)} error={cents[1] === null} fullWidth />
            <TextField label="Fixed charge ($)" value={fixed} onChange={set(setFixed)} error={cents[2] === null} fullWidth />
            <TextField label="Percent charge (%)" value={percent} onChange={set(setPercent)} fullWidth />
          </Stack>
          <TextField label="Instructions for traders" value={instructions} onChange={set(setInstructions)} multiline minRows={2} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <Stack spacing={1}>
            <Typography sx={{ fontWeight: 600 }}>What the trader fills in ({fields.length.toString()} of 10)</Typography>
            {fields.map((f, i) => (
              <Stack key={i} direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ alignItems: "center" }}>
                <TextField size="small" label="Field name (e.g. iban)" value={f.name} onChange={set((v) => { setFields(fields.map((g, j) => (j === i ? { ...g, name: v.trim().toLowerCase() } : g))); })} />
                <TextField size="small" label="Label" value={f.label} onChange={set((v) => { setFields(fields.map((g, j) => (j === i ? { ...g, label: v } : g))); })} />
                <TextField size="small" select label="Type" value={f.type} onChange={set((v) => { setFields(fields.map((g, j) => (j === i ? { ...g, type: v as UserField["type"] } : g))); })}>
                  <MenuItem value="text">Text</MenuItem>
                  <MenuItem value="number">Number</MenuItem>
                  <MenuItem value="email">Email</MenuItem>
                </TextField>
                <FormControlLabel control={<Switch checked={f.required} onChange={(e) => { setFields(fields.map((g, j) => (j === i ? { ...g, required: e.target.checked } : g))); }} />} label="Required" />
                <Button size="small" color="inherit" onClick={() => { setFields(fields.filter((_, j) => j !== i)); }}>Remove</Button>
              </Stack>
            ))}
            {fields.length < 10 ? (
              <div>
                <Button size="small" onClick={() => { setFields([...fields, { name: "", label: "", type: "text", required: true }]); }}>Add a field</Button>
              </div>
            ) : null}
          </Stack>
          <FormControlLabel control={<Switch checked={enabled} onChange={(e) => { setEnabled(e.target.checked); }} />} label="Enabled" />
          <TextField label="Reason (kept with the record)" value={reason} onChange={set(setReason)} />
          {shown}
          <div>
            <Button variant="contained" disabled={!valid || busy} onClick={() => void propose()}>Propose</Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}

function SecretForm({ methods }: { methods: Method[] }) {
  const { api } = useBackOffice();
  const [method, setMethod] = useState(methods[0]?.method_id ?? "");
  const [name, setName] = useState("api_key");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const { setMessage, shown } = useMessage();
  const propose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/admin/payment-methods/${method}/secrets/${name}`, { value, reason });
      setValue("");
      setReason("");
      setMessage({ ok: true, text: "Sealed and proposed. It will never be shown again; an owner approves it." });
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };
  if (methods.length === 0) return null;
  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title="Set a secret" subheader="API keys and similar are encrypted at once and can never be read back; only that they are set is shown." />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField select label="Method" value={method} onChange={(e) => { setMethod(e.target.value); }} fullWidth>
              {methods.map((m) => (
                <MenuItem key={m.method_id} value={m.method_id}>{m.name}</MenuItem>
              ))}
            </TextField>
            <TextField label="Secret name (e.g. api_key)" value={name} onChange={(e) => { setName(e.target.value.trim().toLowerCase()); }} error={!NAME.test(name)} fullWidth />
          </Stack>
          <TextField label="Value" type="password" autoComplete="off" value={value} onChange={(e) => { setValue(e.target.value); }} />
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} />
          {shown}
          <div>
            <Button variant="contained" disabled={busy || value === "" || reason.trim() === "" || !NAME.test(name)} onClick={() => void propose()}>Seal and propose</Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}

/** Payment and withdrawal methods (rules AC02, AC13, AC14). */
export function PaymentMethodsPage() {
  const { result, reload } = useGet<MethodsPage>("/admin/payment-methods");
  const [editing, setEditing] = useState<Method | null>(null);
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Payment methods" />
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <p role="alert">{result.message}</p> : null}
      {result.state === "ready" ? (
        <>
          <Card>
            <CardHeader slotProps={{ title: { component: "h2" } }} title={`Methods (${result.data.methods.length.toString()})`} />
            <CardContent sx={{ pt: 0 }}>
              {result.data.methods.length === 0 ? (
                <Typography color="text.secondary">None attached: the checkout uses its own provider.</Typography>
              ) : (
                <Table size="small" aria-label="payment methods">
                  <TableHead>
                    <TableRow>
                      <TableCell>Method</TableCell>
                      <TableCell>Kind</TableCell>
                      <TableCell>Limits</TableCell>
                      <TableCell>Charge</TableCell>
                      <TableCell>Secrets</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.data.methods.map((m) => (
                      <TableRow key={m.method_id}>
                        <TableCell>{m.name} <Typography component="span" variant="body2" color="text.secondary">({m.adapter})</Typography></TableCell>
                        <TableCell>{m.kind}</TableCell>
                        <TableCell>${dollars(m.min_cents)} to {m.max_cents === 0 ? "no maximum" : `$${dollars(m.max_cents)}`}</TableCell>
                        <TableCell>${dollars(m.fixed_charge_cents)} + {m.percent_charge}%</TableCell>
                        <TableCell>{m.secrets.length === 0 ? "none" : m.secrets.map((s) => `${s}: set`).join(", ")}</TableCell>
                        <TableCell><Chip size="small" label={m.enabled ? "Enabled" : "Disabled"} color={m.enabled ? "success" : "default"} variant="outlined" /></TableCell>
                        <TableCell><Button size="small" onClick={() => { setEditing(m); }}>Change</Button></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
          <MethodForm key={editing?.method_id ?? "new"} page={result.data} start={editing} onDone={() => { setEditing(null); reload(); }} />
          <SecretForm methods={result.data.methods} />
        </>
      ) : null}
    </Stack>
  );
}
