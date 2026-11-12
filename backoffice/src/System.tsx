import { Alert, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableRow, Typography } from "@mui/material";
import { Title } from "react-admin";
import { when } from "./format";
import { useGet } from "./useGet";

export interface SystemInfo {
  application: { name: string; version: string; revision: string; python: string; api_started_at: number };
  parts: { name: string; status: "ok" | "unavailable"; version: string }[];
  journal: { seq: number | null; engine_ts: number | null };
}

/** System information (rule AC23), admins and owners: what is running and
whether each part answers. */
export function SystemPage() {
  const { result } = useGet<SystemInfo>("/admin/system");
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="System information" />
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? <SystemView info={result.data} /> : null}
    </Stack>
  );
}

export function SystemView({ info }: { info: SystemInfo }) {
  const a = info.application;
  const rows: [string, string][] = [
    ["Application", `${a.name} ${a.version}`],
    ["Build", a.revision],
    ["Python", a.python],
    ["API started", when(a.api_started_at)],
    ["Journal head", info.journal.seq === null ? "no events yet" : `event ${info.journal.seq}`],
    ["Engine time", when(info.journal.engine_ts)],
  ];
  return (
    <>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2", variant: "h6" } }} title="Application" />
        <CardContent>
          <Table size="small" aria-label="application">
            <TableBody>
              {rows.map(([k, v]) => (
                <TableRow key={k}>
                  <TableCell component="th" scope="row" sx={{ width: 200 }}>
                    {k}
                  </TableCell>
                  <TableCell>{v}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card>
        <CardHeader slotProps={{ title: { component: "h2", variant: "h6" } }} title="Servers" />
        <CardContent>
          <Stack spacing={1}>
            {info.parts.map((p) => (
              <Stack key={p.name} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Chip size="small" color={p.status === "ok" ? "success" : "error"} label={p.status === "ok" ? "answering" : "not answering"} />
                <Typography>
                  {p.name}: {p.version}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </CardContent>
      </Card>
      <Alert severity="info">
        Updates are released through a reviewed change and a redeploy, never from the console. There is no cache to clear: settings, policies and website text are read fresh each time.
      </Alert>
    </>
  );
}
