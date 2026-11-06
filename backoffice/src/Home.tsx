import { Button, Card, CardContent, CardHeader, Chip, List, ListItem, ListItemText, Stack, Typography } from "@mui/material";
import { useState } from "react";
import { Link, Title } from "react-admin";
import { ApiError } from "./api";
import type { Me } from "./auth";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";

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
  if (t.kind === "access_review" || t.kind === "graduation") return `/members/${t.ref}/show`;
  if (t.kind === "apprentice_proposal") return "/apprentice-proposals";
  if (t.kind === "settings_proposal") return "/proposals";
  if (t.kind === "news_gap") return "/news";
  return null;
}

/** My staff page: access, to-do, notifications, duties, training. */
export function Home() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<StaffPage>("/staff/me");
  const [error, setError] = useState<string | null>(null);
  const post = (path: string) => {
    api.post(path).then(reload, (e: unknown) => {
      setError(e instanceof ApiError ? e.message : "failed");
    });
  };
  if (result.state === "loading") return <p>Loading…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const me = result.data;
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="My work" />
      {error === null ? null : <p role="alert">{error}</p>}
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={me.member?.name ?? "Signed in (no directory record)"} subheader={`${me.access.title ?? "-"} · ${me.access.sid ?? "no member record"}`} />
        <CardContent>
          <Typography variant="subtitle2" component="h2">
            Section levels
          </Typography>
          {Object.keys(me.access.sections).length === 0 ? (
            <Typography variant="body2">None: firm-wide access only.</Typography>
          ) : (
            <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
              {Object.entries(me.access.sections).map(([s, lv]) => (
                <Chip key={s} label={`${s} ${lv}`} />
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`To do (${me.todo.length})`} />
        <List dense>
          {me.todo.length === 0 ? <ListItem>Nothing waiting.</ListItem> : null}
          {me.todo.map((t) => {
            const to = linkFor(t);
            const text = <ListItemText primary={t.title} secondary={`since ${when(t.since)}${t.due_at === null ? "" : ` · due ${when(t.due_at)}`}`} />;
            return (
              <ListItem key={`${t.kind}-${t.ref}`}>
                {to === null ? text : <Link to={to}>{text}</Link>}
              </ListItem>
            );
          })}
        </List>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Notifications (${me.notifications.length})`} />
        <List dense>
          {me.notifications.map((n) => (
            <ListItem key={n.id} sx={{ flexDirection: "column", alignItems: "flex-start" }}>
              <ListItemText primary={`${n.urgent ? "URGENT · " : ""}${n.title}`} secondary={`${when(n.ts)} · ${n.body}`} />
              <Button size="small" variant="outlined" onClick={() => {
                post(`/staff/notifications/${n.id}/ack`);
              }}>
                Acknowledge
              </Button>
            </ListItem>
          ))}
        </List>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title="My duties" />
        <CardContent>
          {me.duties.map((d) => (
            <section key={d.sheet} aria-label={d.title}>
              <Typography variant="h6">
                {d.title} (version {d.version})
              </Typography>
              <ul>
                {d.duties.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
              {d.acknowledged ? (
                <Typography variant="body2">Acknowledged.</Typography>
              ) : (
                <Button variant="contained" size="small" onClick={() => {
                  post(`/staff/me/duties/${d.sheet}/ack`);
                }}>
                  I have read these duties
                </Button>
              )}
            </section>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title="Training" />
        <List dense>
          {me.training.map((t) => (
            <ListItem key={t.section}>
              <ListItemText
                primary={`${t.section}: ${t.title}`}
                secondary={t.status === "valid" ? `valid until ${when(t.valid_until)}` : "not passed: approvals in this section are blocked"}
              />
            </ListItem>
          ))}
        </List>
      </Card>
    </Stack>
  );
}
