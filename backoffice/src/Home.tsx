import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, Stack, TextField, Typography } from "@mui/material";
import type { LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Title } from "react-admin";
import { ApiError } from "./api";
import { ICONS, SIZE, STROKE } from "./icons";
import type { Me } from "./auth";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";
import { age, areaOf, byPriority, plainBody, urgency } from "./work";

const clip = (text: string) => (text.length > 280 ? `${text.slice(0, 277)}…` : text);

interface Todo {
  kind: string;
  ref: string;
  title: string;
  since: number;
  due_at: number | null;
}
interface Duty {
  sheet: string;
  title: string;
  version: number;
  duties: string[];
  acknowledged: boolean;
}
interface Notice {
  id: number;
  kind: string;
  ref: string;
  title: string;
  body: string;
  urgent: boolean;
  ts: number;
}
interface Training {
  section: string;
  title: string;
  status: string;
  valid_until: number | null;
}
export interface StaffPage extends Me {
  todo: Todo[];
  duties: Duty[];
  notifications: Notice[];
  training: Training[];
}

/** Where a to-do item is worked: the record's own screen. */
export function linkFor(t: Pick<Todo, "kind" | "ref">): string | null {
  if (t.kind.startsWith("payout_")) return `/payouts/${t.ref}/show`;
  if (t.kind === "case" || t.kind === "case_decision") return `/cases/${t.ref}/show`;
  if (t.kind === "identity_review") return `/onboardings/${t.ref}/show`;
  if (t.kind === "funded_review") return `/progressions/${t.ref}/show`;
  if (t.kind === "grant" || t.kind === "grant_ending") return "/grants";
  if (["access_review", "graduation", "screening_check", "screening_approve"].includes(t.kind)) {
    return `/members/${t.ref}/show`;
  }
  if (t.kind === "apprentice_proposal") return "/apprentice-proposals";
  if (t.kind === "settings_proposal") return "/proposals";
  if (t.kind === "news_gap") return "/news";
  return null;
}

const AREA_ICON: Record<string, LucideIcon> = {
  Payouts: ICONS.payouts,
  Cases: ICONS.cases,
  Reviews: ICONS.progressions,
  News: ICONS.news,
  Settings: ICONS.settings,
  Staff: ICONS.staff,
  Training: ICONS.training,
  Duties: ICONS.duties,
  "My notes": ICONS.notes,
};

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(Date.now());
    }, 60_000);
    return () => {
      window.clearInterval(t);
    };
  }, []);
  return now;
}

function Stat({ label, value, tone }: { label: string; value: number; tone: "default" | "error" | "warning" | "success" }) {
  const colour = { default: "text.primary", error: "error.main", warning: "warning.main", success: "success.main" }[tone];
  return (
    <Card className="stat">
      <CardContent>
        <Typography variant="h4" component="p" sx={{ fontWeight: 760, color: colour, lineHeight: 1 }}>
          {value}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
          {label}
        </Typography>
      </CardContent>
    </Card>
  );
}

function TodoRow({ t, now, onDone }: { t: Todo; now: number; onDone: (ref: string) => void }) {
  const to = linkFor(t);
  const area = areaOf(t.kind);
  const Icon = AREA_ICON[area] ?? ICONS.staff;
  const u = urgency(t, now);
  const due =
    t.due_at === null ? null : u === "overdue" ? `overdue ${age(now - t.due_at)}` : `due in ${age(t.due_at - now)}`;
  const body = (
    <Box className={`todo todo-${u}`}>
      <Box className="todo-icon" aria-hidden="true">
        <Icon size={SIZE} strokeWidth={STROKE} />
      </Box>
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography sx={{ fontWeight: 600 }}>{t.title}</Typography>
        <Typography variant="body2" color="text.secondary">
          {area} · waiting {age(now - t.since)}
        </Typography>
      </Box>
      {due === null ? null : <Chip size="small" label={due} color={u === "overdue" ? "error" : "warning"} variant={u === "overdue" ? "filled" : "outlined"} />}
      {to === null ? null : <ICONS.next aria-hidden="true" size={SIZE} strokeWidth={STROKE} className="todo-next" />}
      {t.kind === "note" ? (
        <Button
          size="small"
          variant="outlined"
          onClick={() => {
            onDone(t.ref);
          }}
          aria-label={`Done: ${t.title}`}
        >
          Done
        </Button>
      ) : null}
    </Box>
  );
  return <li>{to === null ? body : <Link to={to} className="todo-link">{body}</Link>}</li>;
}

/** A note of my own on the to-do list, optionally due at a local date and time. */
function AddNote({ onAdd }: { onAdd: (text: string, dueAt: number | null) => void }) {
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const submit = () => {
    const t = text.trim();
    if (t === "") return;
    onAdd(t, due === "" ? null : new Date(due).getTime());
    setText("");
    setDue("");
  };
  return (
    <Box
      component="form"
      className="add-note"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <TextField
        size="small"
        label="Add a note"
        value={text}
        slotProps={{ htmlInput: { maxLength: 500 } }}
        onChange={(e) => {
          setText(e.target.value);
        }}
      />
      <TextField
        size="small"
        type="datetime-local"
        label="Due (optional)"
        value={due}
        slotProps={{ inputLabel: { shrink: true } }}
        onChange={(e) => {
          setDue(e.target.value);
        }}
      />
      <Button type="submit" variant="contained" disabled={text.trim() === ""}>
        Add
      </Button>
    </Box>
  );
}

/** My staff page: what to do first, then notifications, duties, training. */
export function Home() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<StaffPage>("/staff/me");
  const [error, setError] = useState<string | null>(null);
  const now = useNow();
  const post = (path: string, body?: unknown) => {
    api.post(path, body).then(reload, (e: unknown) => {
      setError(e instanceof ApiError ? e.message : "failed");
    });
  };
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const me = result.data;
  const todo = byPriority(me.todo, now);
  const overdue = todo.filter((t) => urgency(t, now) === "overdue").length;
  const urgent = me.notifications.filter((n) => n.urgent).length;
  const unread = me.duties.filter((d) => !d.acknowledged).length;
  const blocked = me.training.filter((t) => t.status !== "valid").length;
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="My work" />
      {error === null ? null : <Alert severity="error">{error}</Alert>}
      <Box className="me-head">
        <Box>
          <Typography variant="h4" component="h1" sx={{ fontWeight: 740, letterSpacing: "-0.02em" }}>
            {me.member?.name ?? "Signed in (no directory record)"}
          </Typography>
          <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1, mt: 1 }}>
            <Chip label={me.access.title ?? "-"} color="primary" size="small" />
            <Chip label={me.access.sid ?? "no member record"} size="small" variant="outlined" />
            {Object.entries(me.access.sections).map(([sec, lv]) => (
              <Chip key={sec} label={`${sec} · level ${lv}`} size="small" variant="outlined" />
            ))}
            {Object.keys(me.access.sections).length === 0 ? <Chip label="firm-wide" size="small" variant="outlined" /> : null}
          </Stack>
        </Box>
      </Box>
      <Box className="stats">
        <Stat label="To do" value={todo.length} tone="default" />
        <Stat label="Overdue" value={overdue} tone={overdue > 0 ? "error" : "success"} />
        <Stat label="Urgent notices" value={urgent} tone={urgent > 0 ? "warning" : "success"} />
        <Stat label="Duties to read" value={unread} tone={unread > 0 ? "warning" : "success"} />
        <Stat label="Training blocking approvals" value={blocked} tone={blocked > 0 ? "error" : "success"} />
      </Box>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`To do (${todo.length})`} subheader="Overdue first, then the soonest due, then the longest waiting." />
        <CardContent sx={{ pt: 0 }}>
          <AddNote
            onAdd={(text, dueAt) => {
              post("/staff/me/notes", dueAt === null ? { text } : { text, due_at: dueAt });
            }}
          />
          {todo.length === 0 ? (
            <Typography color="text.secondary">Nothing waiting.</Typography>
          ) : (
            <ul className="todo-list" aria-label="To do">
              {todo.map((t) => (
                <TodoRow
                  key={`${t.kind}-${t.ref}`}
                  t={t}
                  now={now}
                  onDone={(ref) => {
                    post(`/staff/me/notes/${ref}/done`);
                  }}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Notifications (${me.notifications.length})`} />
        <CardContent sx={{ pt: 0 }}>
          {me.notifications.length === 0 ? <Typography color="text.secondary">None.</Typography> : null}
          <Stack spacing={1.5}>
            {me.notifications.map((n) => (
              <Alert
                key={n.id}
                severity={n.urgent ? "warning" : "info"}
                action={
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() => {
                      post(`/staff/notifications/${n.id}/ack`);
                    }}
                  >
                    Acknowledge
                  </Button>
                }
              >
                <Typography sx={{ fontWeight: 650 }}>
                  {n.urgent ? "Urgent · " : ""}
                  {n.title}
                </Typography>
                <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
                  {when(n.ts)} · {clip(plainBody(n.body))}
                </Typography>
              </Alert>
            ))}
          </Stack>
        </CardContent>
      </Card>
      <Box className="two-col">
        <Card>
          <CardHeader slotProps={{ title: { component: "h2" } }} title="My duties" />
          <CardContent sx={{ pt: 0 }}>
            {me.duties.map((d) => (
              <section key={d.sheet} aria-label={d.title} className="duty">
                <Typography variant="h6" component="h3">
                  {d.title} <Typography component="span" color="text.secondary">v{d.version}</Typography>
                </Typography>
                <ul>
                  {d.duties.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
                {d.acknowledged ? (
                  <Chip size="small" color="success" variant="outlined" label="Acknowledged" />
                ) : (
                  <Button
                    variant="contained"
                    size="small"
                    onClick={() => {
                      post(`/staff/me/duties/${d.sheet}/ack`);
                    }}
                  >
                    I have read these duties
                  </Button>
                )}
              </section>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader slotProps={{ title: { component: "h2" } }} title="Training" />
          <CardContent sx={{ pt: 0 }}>
            {me.training.length === 0 ? <Typography color="text.secondary">None required.</Typography> : null}
            <Stack spacing={1}>
              {me.training.map((t) => (
                <Box key={t.section} className="training-row">
                  <Typography sx={{ fontWeight: 600 }}>
                    {t.section}: {t.title}
                  </Typography>
                  {t.status === "valid" ? (
                    <Chip size="small" color="success" variant="outlined" label={`valid until ${when(t.valid_until)}`} />
                  ) : (
                    <Chip size="small" color="error" label="not passed: approvals blocked" />
                  )}
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      </Box>
    </Stack>
  );
}
