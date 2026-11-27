import { Alert, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Title } from "react-admin";
import { when } from "./format";
import { usd } from "./Scaling";
import { useGet } from "./useGet";

type Level = "none" | "halve" | "quarter";

interface Group {
  kind: "symbol" | "bloc";
  name: string;
  scenario: string;
  loss_cents: number;
  limit_cents: number | null;
  ratio: string | null;
  level: Level;
}

interface CutRow {
  symbol: string;
  side: "buy" | "sell";
  level: Exclude<Level, "none">;
  classes: string[];
  since: number;
  under_since: number | null;
}

interface Move {
  seq: number;
  at: number;
  symbol: string;
  side: string;
  event: "raise" | "under" | "over" | "restore";
  level: string;
  ratio: string;
  scenario: string | null;
  policy_version: number;
}

export interface ExposureDesk {
  as_of: number;
  free_reserve_cents: number | null;
  accounts: number;
  unread: string[];
  groups: Group[];
  cuts: CutRow[];
  moves: Move[];
}

const LEVEL: Record<Level, string> = { none: "Within limit", halve: "Lot caps halved", quarter: "Lot caps quartered" };

const EVENT: Record<Move["event"], string> = {
  raise: "Cut",
  under: "Under 60 %",
  over: "Back over 60 %",
  restore: "Restored",
};

/** The share of its limit as a number, for colour: "80.4 %" -> 80.4. */
export function ratioValue(ratio: string | null): number | null {
  if (ratio === null) return null;
  const n = Number.parseFloat(ratio);
  return Number.isNaN(n) ? null : n;
}

export function tone(ratio: string | null): "default" | "warning" | "error" {
  const n = ratioValue(ratio);
  if (n === null || n < 80) return "default";
  return n >= 100 ? "error" : "warning";
}

/** Exposure desk (D-14.32): scenario liability of funded accounts per
symbol and bloc against the free reserve; lot cap cuts and their record. */
export function ExposurePage() {
  const { result } = useGet<ExposureDesk>("/staff/exposure");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Exposure" />
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? <DeskView desk={result.data} /> : null}
    </Stack>
  );
}

function DeskView({ desk }: { desk: ExposureDesk }) {
  return (
    <>
      <Card>
        <CardHeader
          title="Scenario liability"
          subheader={
            desk.free_reserve_cents === null
              ? "The reserve is off: no limits."
              : `Free reserve ${usd(desk.free_reserve_cents)}. One symbol up to 25 %, a bloc up to 50 %. ${String(desk.accounts)} funded accounts with open positions, as of ${when(desk.as_of)}.`
          }
        />
        <CardContent>
          {desk.unread.length > 0 ? (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Not read: {desk.unread.join(", ")}
            </Alert>
          ) : null}
          {desk.groups.length === 0 ? (
            <Typography color="text.secondary">No funded account has an open position.</Typography>
          ) : (
            <Table size="small" aria-label="Scenario liability">
              <TableHead>
                <TableRow>
                  <TableCell>Symbol or bloc</TableCell>
                  <TableCell>Worst scenario</TableCell>
                  <TableCell align="right">Firm would owe more</TableCell>
                  <TableCell align="right">Limit</TableCell>
                  <TableCell>Of limit</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {desk.groups.map((g) => (
                  <TableRow key={`${g.kind}:${g.name}`}>
                    <TableCell>{g.kind === "bloc" ? `${g.name} bloc` : g.name}</TableCell>
                    <TableCell>{g.scenario}</TableCell>
                    <TableCell align="right">{usd(g.loss_cents)}</TableCell>
                    <TableCell align="right">{g.limit_cents === null ? "None" : usd(g.limit_cents)}</TableCell>
                    <TableCell>
                      {g.ratio === null ? "" : <Chip size="small" color={tone(g.ratio)} label={`${g.ratio} · ${LEVEL[g.level]}`} />}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Lot cap cuts" subheader="New orders only; open positions are untouched. Back to baseline after 24 hours under 60 %. Applied by the engine from step 5, line 6." />
        <CardContent>
          {desk.cuts.length === 0 ? (
            <Typography color="text.secondary">No cut in force.</Typography>
          ) : (
            <Table size="small" aria-label="Lot cap cuts">
              <TableHead>
                <TableRow>
                  <TableCell>Symbol</TableCell>
                  <TableCell>Side</TableCell>
                  <TableCell>Cut</TableCell>
                  <TableCell>Classes</TableCell>
                  <TableCell>Since</TableCell>
                  <TableCell>Under 60 % since</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {desk.cuts.map((c) => (
                  <TableRow key={`${c.symbol}:${c.side}`}>
                    <TableCell>{c.symbol}</TableCell>
                    <TableCell>{c.side === "buy" ? "Buy" : "Sell"}</TableCell>
                    <TableCell>{LEVEL[c.level]}</TableCell>
                    <TableCell>{c.classes.join(", ")}</TableCell>
                    <TableCell>{when(c.since)}</TableCell>
                    <TableCell>{c.under_since === null ? "" : when(c.under_since)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {desk.moves.length > 0 ? (
        <Card>
          <CardHeader title="Record" subheader="Every change, with the settings version it was decided under." />
          <CardContent>
            <Table size="small" aria-label="Exposure record">
              <TableHead>
                <TableRow>
                  <TableCell>When</TableCell>
                  <TableCell>Symbol</TableCell>
                  <TableCell>Event</TableCell>
                  <TableCell>Of limit</TableCell>
                  <TableCell>Scenario</TableCell>
                  <TableCell align="right">Settings</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {desk.moves.map((m) => (
                  <TableRow key={m.seq}>
                    <TableCell>{when(m.at)}</TableCell>
                    <TableCell>
                      {m.symbol} {m.side}
                    </TableCell>
                    <TableCell>
                      {EVENT[m.event]} ({m.level})
                    </TableCell>
                    <TableCell>{m.ratio}</TableCell>
                    <TableCell>{m.scenario ?? ""}</TableCell>
                    <TableCell align="right">v{m.policy_version}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}
