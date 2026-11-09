import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

interface EventSpec {
  label: string;
  variables: Record<string, string>;
  required: string[];
  default: { subject: string; body: string };
  sample: Record<string, string>;
}
export interface NoticesView {
  channels: string[];
  events: Record<string, EventSpec>;
  templates: Record<string, { subject: string; body: string; approved: boolean }>;
  wrapper: Record<string, string>;
  default_wrapper: string;
  limits: { subject: number; body: number; wrapper: number };
}

const VAR = /\{([a-z_]{1,40})\}/g;

/** The text with each {variable} filled; unknown ones are left as typed. */
export function fill(text: string, values: Record<string, string>): string {
  return text.replace(VAR, (whole, name: string) => values[name] ?? whole);
}

/** Problems the engine would refuse, shown before proposing (it checks again). */
export function problems(text: string, allowed: readonly string[], required: readonly string[]): string[] {
  const used = new Set([...text.matchAll(VAR)].map((m) => m[1] ?? ""));
  const out: string[] = [];
  for (const v of used) if (!allowed.includes(v)) out.push(`unknown variable {${v}}`);
  for (const v of required) if (!used.has(v)) out.push(`must include {${v}}`);
  if (/[{}]/.test(text.replace(VAR, ""))) out.push("braces are only for variables");
  return out;
}

function useProposal(onDone: () => void) {
  const { api } = useBackOffice();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const send = async (path: string, body: object): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(path, body);
      setMessage({ ok: true, text: "Proposed. A second admin or owner approves it under Setting proposals." });
      onDone();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };
  return { busy, message, send };
}

function Preview({ subject, body }: { subject?: string; body: string }) {
  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 2, p: 2, bgcolor: "action.hover" }} aria-label="Preview">
      <Typography variant="overline" color="text.secondary">
        Preview with sample values
      </Typography>
      {subject === undefined ? null : (
        <Typography sx={{ fontWeight: 600, mb: 1 }}>{subject}</Typography>
      )}
      <Typography component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 14 }}>
        {body}
      </Typography>
    </Box>
  );
}

function EventEditor({ name, data, onDone }: { name: string; data: NoticesView; onDone: () => void }) {
  const spec = data.events[name];
  const current = data.templates[name];
  const [subject, setSubject] = useState(current?.subject ?? "");
  const [body, setBody] = useState(current?.body ?? "");
  const [reason, setReason] = useState("");
  const { busy, message, send } = useProposal(onDone);
  if (spec === undefined || current === undefined) return null;
  const allowed = Object.keys(spec.variables);
  const issues = [...problems(subject, allowed, []), ...problems(body, allowed, spec.required.filter((v) => !subject.includes(`{${v}}`)))];
  const same = subject === current.subject && body === current.body;
  const wrapped = fill(data.wrapper.email ?? "{body}", { body: fill(body, spec.sample) });
  return (
    <Card>
      <CardHeader
        slotProps={{ title: { component: "h2" } }}
        title={spec.label}
        action={<Chip size="small" label={current.approved ? "Approved wording" : "Built-in wording"} variant="outlined" sx={{ mt: 1, mr: 1 }} />}
      />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
            {Object.entries(spec.variables).map(([v, what]) => (
              <Tooltip key={v} title={what}>
                <Chip size="small" label={`{${v}}${spec.required.includes(v) ? " required" : ""}`} variant={spec.required.includes(v) ? "filled" : "outlined"} />
              </Tooltip>
            ))}
          </Stack>
          <TextField label="Subject" value={subject} onChange={(e) => { setSubject(e.target.value); }} slotProps={{ htmlInput: { maxLength: data.limits.subject } }} />
          <TextField label="Body" value={body} onChange={(e) => { setBody(e.target.value); }} multiline minRows={8} slotProps={{ htmlInput: { maxLength: data.limits.body } }} helperText={`${body.length.toString()}/${data.limits.body.toString()}`} />
          {issues.length > 0 ? <Alert severity="warning">{issues.join("; ")}</Alert> : null}
          <Preview subject={fill(subject, spec.sample)} body={wrapped} />
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
          {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          <Stack direction="row" spacing={1}>
            <Button variant="contained" disabled={busy || same || issues.length > 0 || reason.trim() === ""} onClick={() => void send(`/admin/notices/${name}`, { subject, body, reason })}>
              Propose
            </Button>
            <Button
              disabled={subject === spec.default.subject && body === spec.default.body}
              onClick={() => {
                setSubject(spec.default.subject);
                setBody(spec.default.body);
              }}
            >
              Use the built-in wording
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}

function WrapperEditor({ data, onDone }: { data: NoticesView; onDone: () => void }) {
  const current = data.wrapper.email ?? data.default_wrapper;
  const [text, setText] = useState(current);
  const [reason, setReason] = useState("");
  const { busy, message, send } = useProposal(onDone);
  const issues = problems(text, ["body"], ["body"]);
  if ((text.match(/\{body\}/g) ?? []).length > 1) issues.push("include {body} only once");
  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title="Email wrapper" subheader="The text around every email: a header line, a footer. {body} marks where each email's own text goes." />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          <TextField label="Wrapper" value={text} onChange={(e) => { setText(e.target.value); }} multiline minRows={4} slotProps={{ htmlInput: { maxLength: data.limits.wrapper } }} />
          {issues.length > 0 ? <Alert severity="warning">{issues.join("; ")}</Alert> : null}
          <Preview body={fill(text, { body: "(the email's own text)" })} />
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
          {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          <div>
            <Button variant="contained" disabled={busy || text === current || issues.length > 0 || reason.trim() === ""} onClick={() => void send("/admin/notice-wrapper/email", { text, reason })}>
              Propose
            </Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}

/** Notification templates (rule AC12): each email's wording and the wrapper, by proposal. */
export function NoticesPage() {
  const { result, reload } = useGet<NoticesView>("/admin/notices");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Notification templates" />
      <Typography color="text.secondary">
        Emails the firm sends traders. Until wording is approved here, the built-in wording is sent. Every change is proposed and approved by a second admin or owner. Email is the only channel today.
      </Typography>
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <p role="alert">{result.message}</p> : null}
      {result.state === "ready" ? (
        <>
          <WrapperEditor key={`wrapper:${result.data.wrapper.email ?? ""}`} data={result.data} onDone={reload} />
          {Object.keys(result.data.events).map((name) => (
            <EventEditor key={`${name}:${JSON.stringify(result.data.templates[name] ?? null)}`} name={name} data={result.data} onDone={reload} />
          ))}
        </>
      ) : null}
    </Stack>
  );
}
