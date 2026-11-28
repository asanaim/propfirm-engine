import { Alert, Button, Card, CardContent, CardHeader, Chip, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

export interface SlotState {
  provider: string;
  enabled: boolean;
  public_id: string;
  /** null: the slot has no secret; else whether it is set. */
  secret: boolean | null;
}

export type Slots = Record<string, SlotState>;

const ABOUT: Record<string, { title: string; id: string; help: string }> = {
  chat: { title: "Live chat", id: "Crisp website ID", help: "Crisp's chat box on every portal page. Free plan; the ID is in Crisp under Settings, Website settings." },
  captcha: {
    title: "Captcha",
    id: "Turnstile site key",
    help: "Cloudflare Turnstile on public forms (the newsletter form). Free. Its secret key is approved first, then the captcha is switched on.",
  },
  analytics: { title: "Analytics", id: "Web Analytics token", help: "Cloudflare Web Analytics: page views without cookies. Free." },
};

/** Extensions (rule AC22, D-15.3): live chat, captcha and analytics. Each
change is a proposal a second admin or owner approves (an owner, for a
secret); a secret is
encrypted at once and never shown again. */
export function ExtensionsPage() {
  const { result, reload } = useGet<Slots>("/admin/extensions");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Extensions" />
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready"
        ? Object.entries(result.data).map(([slot, s]) => <SlotCard key={slot} slot={slot} state={s} done={reload} />)
        : null}
    </Stack>
  );
}

/** The warning a slot needs, if any. */
export function warning(s: SlotState): string | null {
  return s.secret === false ? "Propose its secret key first; an owner approves it. Then it can be switched on." : null;
}

function SlotCard({ slot, state, done }: { slot: string; state: SlotState; done: () => void }) {
  const { api } = useBackOffice();
  const about = ABOUT[slot] ?? { title: slot, id: "Public ID", help: "" };
  const [enabled, setEnabled] = useState(state.enabled);
  const [publicId, setPublicId] = useState(state.public_id);
  const [secret, setSecret] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const changed = enabled !== state.enabled || publicId.trim() !== state.public_id;

  async function put(path: string, body: unknown, after: () => void, approver: string) {
    setMessage(null);
    try {
      await api.put(path, body);
      setMessage({ ok: true, text: `Proposed: ${approver} approves it on Proposals.` });
      after();
      done();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    }
  }

  const warn = warning(state);
  return (
    <Card>
      <CardHeader
        title={about.title}
        subheader={about.help}
        action={<Chip size="small" color={state.enabled ? "success" : "default"} label={`${state.provider}: ${state.enabled ? "on" : "off"}`} />}
      />
      <CardContent>
        <Stack spacing={2}>
          {warn ? <Alert severity="warning">{warn}</Alert> : null}
          {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
          <FormControlLabel
            control={
              <Switch
                checked={enabled}
                onChange={(e) => {
                  setEnabled(e.target.checked);
                }}
              />
            }
            label="On"
          />
          <TextField
            label={about.id}
            value={publicId}
            onChange={(e) => {
              setPublicId(e.target.value);
            }}
          />
          {state.secret !== null ? (
            <TextField
              label={`Secret key (${state.secret ? "set" : "not set"})`}
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => {
                setSecret(e.target.value);
              }}
            />
          ) : null}
          <TextField
            label="Reason"
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
          <Stack direction="row" spacing={1}>
            <Button
              variant="contained"
              disabled={!changed || reason.trim() === "" || (enabled && (publicId.trim() === "" || state.secret === false))}
              onClick={() => {
                void put(`/admin/extensions/${slot}`, { enabled, public_id: publicId.trim(), reason: reason.trim() }, () => {
                  setReason("");
                }, "a second admin or owner");
              }}
            >
              Propose change
            </Button>
            {state.secret !== null ? (
              <Button
                variant="outlined"
                disabled={secret === "" || reason.trim() === ""}
                onClick={() => {
                  void put(`/admin/extensions/${slot}/secret`, { value: secret, reason: reason.trim() }, () => {
                    setSecret("");
                    setReason("");
                  }, "an owner");
                }}
              >
                Propose secret
              </Button>
            ) : null}
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}
