import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { money } from "./format";
import { useGet } from "./useGet";

export interface OfferingView {
  products: { id: string; name: string; on: boolean; instant: boolean; sizes: { size_cents: number; price_cents: number; on: boolean }[] }[];
  sizes: { size_cents: number; on: boolean }[];
  allocation_cap_cents: number;
  one_instant_funding_per_trader: boolean;
  unpaid_hold_hours: number;
  addons?: { id: string; price_percent: string; products: string[]; on: boolean }[];
}

/** What each add-on does (D-13.18). */
export const ADDON_TEXT: Record<string, string> = {
  split90: "90% profit split from the first payout",
  swing: "Swing: hold over news and weekends, leverage at most 1:30",
  swapfree: "Swap free for the first 5 nights of each position",
};

/** "10K", "200K" for an account size in cents. */
export function sizeLabel(cents: number): string {
  return `${Math.trunc(cents / 100_000)}K`;
}

/** What to propose: the products, sizes and add-ons switched off. */
export function proposal(productsOn: Record<string, boolean>, sizesOn: Record<number, boolean>, reason: string, addonsOn: Record<string, boolean> = {}) {
  return {
    products_off: Object.entries(productsOn)
      .filter(([, on]) => !on)
      .map(([id]) => id)
      .sort(),
    sizes_off: Object.entries(sizesOn)
      .filter(([, on]) => !on)
      .map(([size]) => Number(size))
      .sort((a, b) => a - b),
    // left out when the screen knows no add-ons: the server keeps their switches
    ...(Object.keys(addonsOn).length > 0
      ? {
          addons_off: Object.entries(addonsOn)
            .filter(([, on]) => !on)
            .map(([id]) => id)
            .sort(),
        }
      : {}),
    reason,
  };
}

/** Products and account sizes on sale (review D4): owners and admins
switch them; a second admin or owner approves. Stops new sales only. */
export function OfferingPage() {
  const { result, reload } = useGet<OfferingView>("/admin/offering");
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="Products, sizes and add-ons" />
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {/* keyed by what the server holds, so a reload starts the switches afresh */}
      {result.state === "ready" ? <OfferingEditor key={JSON.stringify(result.data)} view={result.data} onDone={reload} /> : null}
    </Stack>
  );
}

export function OfferingEditor({ view, onDone }: { view: OfferingView; onDone: () => void }) {
  const { api } = useBackOffice();
  const [products, setProducts] = useState<Record<string, boolean>>(() => Object.fromEntries(view.products.map((p) => [p.id, p.on])));
  const [sizes, setSizes] = useState<Record<number, boolean>>(() => Object.fromEntries(view.sizes.map((s) => [s.size_cents, s.on])));
  const [addons, setAddons] = useState<Record<string, boolean>>(() => Object.fromEntries((view.addons ?? []).map((a) => [a.id, a.on])));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const changed = view.products.some((p) => products[p.id] !== p.on) || view.sizes.some((s) => sizes[s.size_cents] !== s.on) || (view.addons ?? []).some((a) => addons[a.id] !== a.on);
  const propose = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put("/admin/offering", proposal(products, sizes, reason.trim(), addons));
      setMessage({ ok: true, text: "Proposed. A second admin or owner approves it under Setting proposals." });
      setReason("");
      onDone();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Card>
        <CardHeader title="Rules for every purchase" />
        <CardContent>
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            <Chip label={`Allocation cap: ${money(view.allocation_cap_cents)} per trader`} />
            <Chip label={view.one_instant_funding_per_trader ? "One Instant account per trader" : "Several Instant accounts allowed"} />
            <Chip label={`Unpaid orders count for ${view.unpaid_hold_hours} hours`} />
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Open accounts, accounts waiting for their next stage, paid orders and recent unpaid orders all count towards the cap. These rules are set in the policy file.
          </Typography>
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Account sizes" subheader="A size switched off is not sold in any product." />
        <CardContent>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            {view.sizes.map((s) => (
              <FormControlLabel
                key={s.size_cents}
                control={
                  <Switch
                    checked={sizes[s.size_cents] ?? s.on}
                    onChange={(e) => {
                      setSizes({ ...sizes, [s.size_cents]: e.target.checked });
                    }}
                  />
                }
                label={sizeLabel(s.size_cents)}
              />
            ))}
          </Stack>
          {!view.sizes.some((s) => s.size_cents === 40_000_000) ? (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              400K: no product has a 400K price yet. An owner adds the size and its price in the policy file; it then appears here.
            </Typography>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Products" subheader="A product switched off is not sold; accounts already bought keep their rules." />
        <CardContent>
          <Stack spacing={1.5}>
            {view.products.map((p) => (
              <Box key={p.id}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={products[p.id] ?? p.on}
                      onChange={(e) => {
                        setProducts({ ...products, [p.id]: e.target.checked });
                      }}
                    />
                  }
                  label={`${p.name}${p.instant ? " (straight to funded)" : ""}`}
                />
                <Typography variant="body2" color="text.secondary">
                  {p.sizes.map((s) => `${sizeLabel(s.size_cents)} ${money(s.price_cents)}${(sizes[s.size_cents] ?? s.on) ? "" : " (off)"}`).join(" · ")}
                </Typography>
              </Box>
            ))}
          </Stack>
        </CardContent>
      </Card>
      {(view.addons ?? []).length > 0 ? (
        <Card>
          <CardHeader title="Add-ons" subheader="Priced as a share of the list price. An add-on switched off is not sold; accounts that have it keep it." />
          <CardContent>
            <Stack spacing={1.5}>
              {(view.addons ?? []).map((a) => {
                const names = a.products.map((id) => view.products.find((p) => p.id === id)?.name ?? id).join(", ");
                return (
                  <Box key={a.id}>
                    <FormControlLabel
                      control={
                        <Switch
                          checked={addons[a.id] ?? a.on}
                          onChange={(e) => {
                            setAddons({ ...addons, [a.id]: e.target.checked });
                          }}
                        />
                      }
                      label={`${ADDON_TEXT[a.id] ?? a.id} (+${a.price_percent}%)`}
                    />
                    <Typography variant="body2" color="text.secondary">
                      {names}
                    </Typography>
                  </Box>
                );
              })}
            </Stack>
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardContent>
          <Stack spacing={1.5}>
            <TextField
              label="Reason"
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
              }}
              size="small"
              fullWidth
            />
            <Box>
              <Button variant="contained" disabled={!changed || busy || reason.trim().length < 3} onClick={() => void propose()}>
                Propose the change
              </Button>
            </Box>
            {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
          </Stack>
        </CardContent>
      </Card>
    </>
  );
}
