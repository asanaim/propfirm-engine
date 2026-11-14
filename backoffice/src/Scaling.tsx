import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

interface Step {
  scl: string;
  acc: string;
  step: number;
  add_cents: number;
  status: "proposed" | "applied" | "rejected";
  proposed_at: number;
  decided_by: string | null;
  measures: { checks: { name: string; ok: boolean; detail: string }[] };
}

export function usd(cents: number): string {
  return `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const NAMES: Record<string, string> = {
  period: "Review period",
  net_profit: "Net profit",
  paid_payouts: "Paid payouts",
  class: "Class A or B",
  active: "Account active",
  allocation: "Within the allocation cap",
};

/** Scaling steps (rule D-14.8): proposed by the hourly check when all
conditions hold; one member of staff approves and the engine adds the
allocation. */
export function ScalingPage() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<Step[]>("/staff/scaling");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const act = async (path: string, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.post(path, {});
      setMessage({ ok: true, text: done });
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Scaling" />
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
          The check runs every hour over funded Lite, Classic and 1-Step accounts and proposes a step when every condition holds.
        </Typography>
        <Button variant="outlined" disabled={busy} onClick={() => void act("/staff/scaling/run", "Checked: new proposals are listed.")}>
          Check now
        </Button>
      </Stack>
      {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" && result.data.length === 0 ? <Typography variant="body2">No scaling steps yet.</Typography> : null}
      {result.state === "ready"
        ? result.data.map((s) => (
            <Card key={s.scl}>
              <CardHeader
                slotProps={{ title: { component: "h2" } }}
                title={`${s.scl} · ${s.acc} · step ${s.step.toString()} · +${usd(s.add_cents)}`}
                subheader={s.decided_by === null ? "Proposed by the scaling check" : `${s.status} by ${s.decided_by}`}
                action={<Chip label={s.status} color={s.status === "applied" ? "success" : s.status === "rejected" ? "default" : "warning"} />}
              />
              <CardContent sx={{ pt: 0 }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Condition</TableCell>
                      <TableCell>Met</TableCell>
                      <TableCell>Measured</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {s.measures.checks.map((c) => (
                      <TableRow key={c.name}>
                        <TableCell>{NAMES[c.name] ?? c.name}</TableCell>
                        <TableCell>{c.ok ? "Yes" : "No"}</TableCell>
                        <TableCell>{c.detail}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {s.status === "proposed" ? (
                  <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                    <Button variant="contained" disabled={busy} onClick={() => void act(`/staff/scaling/${s.scl}/approve`, `${s.scl} applied.`)}>
                      Approve and add
                    </Button>
                    <Button disabled={busy} onClick={() => void act(`/staff/scaling/${s.scl}/reject`, `${s.scl} rejected.`)}>
                      Reject
                    </Button>
                  </Stack>
                ) : null}
              </CardContent>
            </Card>
          ))
        : null}
    </Stack>
  );
}
