import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { when } from "./format";
import { useGet } from "./useGet";

type Status = "pending" | "confirmed" | "unsubscribed";

export interface Consent {
  email: string;
  status: Status;
  subscribed_at: number;
  source: string;
  wording: string;
  wording_version: string;
  country: string | null;
  confirmed_at: number | null;
  unsubscribed_at: number | null;
}

const COLOUR: Record<Status, "success" | "warning" | "default"> = { confirmed: "success", pending: "warning", unsubscribed: "default" };

/** The confirmed list as CSV, with each consent's record (rule AC25). */
export function toCsv(rows: Consent[]): string {
  const quote = (v: string) => (/[",\n]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v);
  const head = "email,subscribed_at,confirmed_at,source,wording_version,country";
  const body = rows
    .filter((r) => r.status === "confirmed")
    .map((r) => [r.email, String(r.subscribed_at), String(r.confirmed_at ?? ""), r.source, r.wording_version, r.country ?? ""].map(quote).join(","));
  return [head, ...body].join("\n") + "\n";
}

/** Subscribers with their consent records (rule AC25): double opt-in, the
wording each one saw kept with it. */
export function SubscribersPage() {
  const [status, setStatus] = useState<Status | "all">("confirmed");
  const { result } = useGet<Consent[]>(status === "all" ? "/staff/subscribers" : `/staff/subscribers?status=${status}`);
  function download(rows: Consent[]) {
    const url = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "subscribers.csv";
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Subscribers" />
      <Card>
        <CardHeader
          title="Newsletter"
          subheader="Only confirmed addresses may be mailed. Each keeps when, where and the exact wording they agreed to."
          action={
            result.state === "ready" && status === "confirmed" ? (
              <Button
                size="small"
                onClick={() => {
                  download(result.data);
                }}
              >
                Export CSV
              </Button>
            ) : null
          }
        />
        <CardContent>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={status}
            onChange={(_, v: Status | "all" | null) => {
              if (v !== null) setStatus(v);
            }}
            sx={{ mb: 2 }}
          >
            <ToggleButton value="confirmed">Confirmed</ToggleButton>
            <ToggleButton value="pending">Waiting</ToggleButton>
            <ToggleButton value="unsubscribed">Left</ToggleButton>
            <ToggleButton value="all">All</ToggleButton>
          </ToggleButtonGroup>
          {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
          {result.state === "ready" && result.data.length === 0 ? <Typography color="text.secondary">No one here.</Typography> : null}
          {result.state === "ready" && result.data.length > 0 ? (
            <Table size="small" aria-label="Subscribers">
              <TableHead>
                <TableRow>
                  <TableCell>Email</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell>Asked</TableCell>
                  <TableCell>Confirmed</TableCell>
                  <TableCell>Where</TableCell>
                  <TableCell>Wording seen</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {result.data.map((c) => (
                  <TableRow key={c.email}>
                    <TableCell>{c.email}</TableCell>
                    <TableCell>
                      <Chip size="small" color={COLOUR[c.status]} label={c.status} />
                    </TableCell>
                    <TableCell>{when(c.subscribed_at)}</TableCell>
                    <TableCell>{c.confirmed_at === null ? "" : when(c.confirmed_at)}</TableCell>
                    <TableCell>
                      {c.source}
                      {c.country ? `, ${c.country}` : ""}
                    </TableCell>
                    <TableCell title={c.wording}>v {c.wording_version}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </Stack>
  );
}
