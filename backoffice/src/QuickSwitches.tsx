import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { useCallback, useContext, useEffect, useState } from "react";
import { ApiError } from "./api";
import { BackOfficeContext } from "./context";

/** One on/off setting shown on a settings card. */
export interface Toggle {
  key: string;
  label: string;
  on: boolean;
}

/** A proposed change: what to send, and the words that describe it. */
interface Pending {
  toggle: Toggle;
  send: (reason: string) => Promise<unknown>;
}

interface SiteStatus {
  maintenance: boolean;
  registration_open: boolean;
}

interface OfferingView {
  products: { id: string; name: string; on: boolean; sizes: { size_cents: number; on: boolean }[] }[];
  sizes: { size_cents: number; on: boolean }[];
}

interface Method {
  method_id: string;
  kind: "payment" | "withdrawal";
  name: string;
  enabled: boolean;
  [field: string]: unknown;
}

/** The switches a settings card shows, by area; null for an area without any. */
export const SWITCHED = new Set(["maintenance", "offering", "payment", "withdrawal"]);

const METHOD_FIELDS = ["kind", "name", "adapter", "enabled", "min_cents", "max_cents", "fixed_charge_cents", "percent_charge", "instructions", "user_fields"];

/** A method as the change body takes it: its fields, without id or secrets. */
export function methodBody(m: Method): Record<string, unknown> {
  return Object.fromEntries(METHOD_FIELDS.map((k) => [k, m[k]]));
}

/** The product ids off after one product is switched. */
export function productsOff(view: OfferingView, id: string, on: boolean): string[] {
  const off = new Set(view.products.filter((p) => !p.on).map((p) => p.id));
  if (on) off.delete(id);
  else off.add(id);
  return [...off].sort();
}

function useToggles(area: string): { toggles: Toggle[]; change: (t: Toggle) => (reason: string) => Promise<unknown>; error: string | null; reload: () => void } {
  const api = useContext(BackOfficeContext)?.api ?? null;
  const [toggles, setToggles] = useState<Toggle[]>([]);
  const [change, setChange] = useState<(t: Toggle) => (reason: string) => Promise<unknown>>(() => () => () => Promise.resolve());
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => {
    setTick((n) => n + 1);
  }, []);
  useEffect(() => {
    if (api === null) return;
    let live = true;
    const done = (list: Toggle[], fn: (t: Toggle) => (reason: string) => Promise<unknown>) => {
      if (!live) return;
      setToggles(list);
      setChange(() => fn);
    };
    const fail = (exc: unknown) => {
      if (live) setError(exc instanceof ApiError ? exc.message : "Could not read the current setting.");
    };
    if (area === "maintenance") {
      api.get<SiteStatus>("/site/status").then((s) => {
        done(
          [
            { key: "maintenance", label: "Maintenance mode", on: s.maintenance },
            { key: "registration", label: "Registration open", on: s.registration_open },
          ],
          (t) => (reason) =>
            api.put("/admin/site-mode", {
              maintenance: t.key === "maintenance" ? !t.on : s.maintenance,
              registration_open: t.key === "registration" ? !t.on : s.registration_open,
              reason,
            }),
        );
      }, fail);
    } else if (area === "offering") {
      api.get<OfferingView>("/admin/offering").then((v) => {
        done(
          v.products.map((p) => ({ key: p.id, label: `${p.name} on sale`, on: p.on })),
          (t) => (reason) =>
            api.put("/admin/offering", {
              products_off: productsOff(v, t.key, !t.on),
              sizes_off: v.sizes.filter((s) => !s.on).map((s) => s.size_cents),
              reason,
            }),
        );
      }, fail);
    } else if (area === "payment" || area === "withdrawal") {
      api.get<{ methods: Method[] }>("/admin/payment-methods").then((r) => {
        const mine = r.methods.filter((m) => m.kind === area);
        done(
          mine.map((m) => ({ key: m.method_id, label: m.name, on: m.enabled })),
          (t) => (reason) => {
            const m = mine.find((x) => x.method_id === t.key);
            if (m === undefined) return Promise.reject(new Error("unknown method"));
            return api.put(`/admin/payment-methods/${m.method_id}`, { ...methodBody(m), enabled: !t.on, reason });
          },
        );
      }, fail);
    }
    return () => {
      live = false;
    };
  }, [api, area, tick]);
  return { toggles, change, error, reload };
}

/** On/off switches on a System settings card (owner remark, 14 Nov 2026).
Flipping one asks for a reason and proposes the change; it takes effect
when a second admin or owner approves it, as every setting does. */
export function QuickSwitches({ area }: { area: string }) {
  const { toggles, change, error, reload } = useToggles(area);
  const [pending, setPending] = useState<Pending | null>(null);
  const [proposed, setProposed] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  if (error !== null) return <Typography variant="caption" color="error">{error}</Typography>;
  if (toggles.length === 0) return null;
  const send = async () => {
    if (pending === null) return;
    setBusy(true);
    setFailure(null);
    try {
      await pending.send(reason);
      setProposed((s) => new Set(s).add(pending.toggle.key));
      setPending(null);
      setReason("");
      reload();
    } catch (exc) {
      setFailure(exc instanceof ApiError ? exc.message : "The proposal failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Box sx={{ px: 2, pb: 1 }}>
      <Stack spacing={0.25}>
        {toggles.map((t) => (
          <Stack key={t.key} direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={t.on}
                  onChange={() => {
                    setFailure(null);
                    setPending({ toggle: t, send: change(t) });
                  }}
                />
              }
              label={<Typography variant="body2">{t.label}</Typography>}
            />
            {proposed.has(t.key) ? <Chip size="small" color="warning" variant="outlined" label="Awaiting approval" /> : null}
          </Stack>
        ))}
      </Stack>
      <Dialog
        open={pending !== null}
        onClose={() => {
          setPending(null);
        }}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>
          {pending === null ? "" : `Turn ${pending.toggle.on ? "off" : "on"}: ${pending.toggle.label}`}
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            This is proposed now and takes effect when a second admin or owner approves it under Setting proposals.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            label="Reason (kept with the record)"
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
            }}
            slotProps={{ htmlInput: { maxLength: 500 } }}
          />
          {failure === null ? null : <Alert severity="error" sx={{ mt: 2 }}>{failure}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setPending(null);
            }}
          >
            Cancel
          </Button>
          <Button variant="contained" disabled={busy || reason.trim().length < 3} onClick={() => void send()}>
            Propose
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
