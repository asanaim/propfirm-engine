import { Alert, Button, Card, CardContent, CardHeader, MenuItem, Stack, TextField, ToggleButton, ToggleButtonGroup } from "@mui/material";
import { useState } from "react";
import { ApiError } from "./api";
import { useBackOffice } from "./context";

export const CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"] as const;

/** A datetime-local value ("2026-11-20T13:30"), read as UTC, in ms; null when not a time. */
export function utcMs(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (m === null) return null;
  const ms = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  return Number.isNaN(ms) ? null : ms;
}

/** An event id the engine accepts (lower case letters, digits and dashes,
at most 64), from the title and the release time: "us-cpi-2026-11-20-1330". */
export function eventId(title: string, ts: number | null): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
  const stamp = ts === null ? "now" : new Date(ts).toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
  return `${slug === "" ? "event" : slug}-${stamp}`.slice(0, 64);
}

type Kind = "scheduled" | "published";

/** Add a release the collector missed (a source it could not read, or an
event outside the calendars): a scheduled one blocks trading on its
symbols from 2 minutes before to 2 minutes after its time; one just
published with no fixed time blocks the next 2 minutes. One person, with a
reason kept in the record; an admin can cancel it. */
export function NewsEntry({ onDone }: { onDone: () => void }) {
  const { api } = useBackOffice();
  const [kind, setKind] = useState<Kind>("scheduled");
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState<"currency" | "oil_inventory">("currency");
  const [currency, setCurrency] = useState<string>("USD");
  const [at, setAt] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const ts = utcMs(at);
  const ready = title.trim() !== "" && reason.trim() !== "" && (kind === "published" || ts !== null);
  const add = async (): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      if (kind === "published") {
        await api.post("/staff/news/published", { event_id: eventId(title, Date.now()), title: title.trim(), currency, reason });
      } else {
        const body = { event_id: eventId(title, ts), title: title.trim(), scope, currency: scope === "currency" ? currency : null, ts, reason };
        await api.post("/staff/news/events", body);
      }
      setMessage({ ok: true, text: "Added: the engine blocks trading on the affected symbols for its window." });
      setTitle("");
      setAt("");
      setReason("");
      onDone();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The event was not added." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader
        slotProps={{ title: { component: "h2" } }}
        title="Add an event by hand"
        subheader="For a release the collector did not fetch. Times are UTC. The window and the affected symbols follow the news rule."
      />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={kind}
            onChange={(_, v: Kind | null) => {
              if (v !== null) setKind(v);
            }}
            aria-label="Kind of event"
          >
            <ToggleButton value="scheduled">Scheduled release</ToggleButton>
            <ToggleButton value="published">Just published (no fixed time)</ToggleButton>
          </ToggleButtonGroup>
          <TextField label="Title" value={title} onChange={(e) => { setTitle(e.target.value); }} slotProps={{ htmlInput: { maxLength: 200 } }} placeholder="US consumer prices (CPI)" />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            {kind === "scheduled" ? (
              <TextField select label="Affects" value={scope} onChange={(e) => { setScope(e.target.value as "currency" | "oil_inventory"); }} sx={{ minWidth: 220 }}>
                <MenuItem value="currency">A currency's symbols</MenuItem>
                <MenuItem value="oil_inventory">Oil (EIA inventory report)</MenuItem>
              </TextField>
            ) : null}
            {kind === "published" || scope === "currency" ? (
              <TextField select label="Currency" value={currency} onChange={(e) => { setCurrency(e.target.value); }} sx={{ minWidth: 140 }}>
                {CURRENCIES.map((c) => (
                  <MenuItem key={c} value={c}>
                    {c}
                  </MenuItem>
                ))}
              </TextField>
            ) : null}
            {kind === "scheduled" ? (
              <TextField
                label="Release time (UTC)"
                type="datetime-local"
                value={at}
                onChange={(e) => { setAt(e.target.value); }}
                slotProps={{ inputLabel: { shrink: true } }}
                helperText={ts === null ? "Date and time of the release, in UTC" : `Window ${new Date(ts - 120_000).toISOString().slice(11, 16)} to ${new Date(ts + 120_000).toISOString().slice(11, 16)} UTC`}
              />
            ) : null}
          </Stack>
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} placeholder="BLS schedule not read by the collector" />
          {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          <div>
            <Button variant="contained" disabled={busy || !ready} onClick={() => void add()}>
              Add event
            </Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}
