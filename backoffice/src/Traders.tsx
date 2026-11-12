import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import { Title } from "react-admin";
import { Link, useSearchParams } from "react-router-dom";
import { money, when } from "./format";
import { useGet } from "./useGet";

/** Segments in the order the tabs show them (the engine's SEGMENTS). */
export const SEGMENTS = [
  ["all", "All"],
  ["trading", "Trading"],
  ["funded", "Funded"],
  ["no_account", "No account"],
  ["kyc_pending", "Identity pending"],
  ["kyc_declined", "Identity declined"],
  ["open_case", "Open case"],
  ["blocked", "Blocked"],
] as const;
export type Segment = (typeof SEGMENTS)[number][0];

export interface TraderRow {
  cif: string;
  full_name: string;
  email: string;
  residence_country: string;
  status: string;
  created_at: number;
  accounts: number;
  funded: number;
  onboarding: string | null;
  open_case: string | null;
  paid_cents: number;
}
export interface TraderList {
  counts: Record<Segment, number>;
  traders: TraderRow[];
}
type Row = Record<string, string | number | null>;
export interface TraderFile {
  trader: TraderRow;
  accounts: Row[];
  orders: Row[];
  onboardings: Row[];
  cases: Row[];
  payouts: Row[];
  notifications: Row[];
  events: Row[];
}

/** The query path for a segment and a search (the API needs 2 to 80 characters). */
export function listPath(segment: Segment, q: string): string {
  const params = new URLSearchParams({ segment, limit: "200" });
  const term = q.trim();
  if (term.length >= 2) params.set("q", term.slice(0, 80));
  return `/staff/traders?${params.toString()}`;
}

/** Waits until typing pauses before searching. */
function useSettled(value: string, ms = 300): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => {
      setSettled(value);
    }, ms);
    return () => {
      clearTimeout(t);
    };
  }, [value, ms]);
  return settled;
}

/** Traders by status, and one trader's file (rule AC19). The file opens
on the same screen (?cif=), so the list keeps its place. */
export function TradersPage() {
  const [params] = useSearchParams();
  const cif = params.get("cif");
  return cif && /^CIF[0-9]{8}$/.test(cif) ? <TraderFilePage cif={cif} /> : <TraderListPage />;
}

function TraderListPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("segment");
  const segment: Segment = SEGMENTS.some(([k]) => k === raw) ? (raw as Segment) : "all";
  const [typed, setTyped] = useState(params.get("q") ?? "");
  const q = useSettled(typed);
  useEffect(() => {
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        if (q.trim()) next.set("q", q.trim());
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [q, setParams]);
  const { result } = useGet<TraderList>(listPath(segment, q));
  const counts = result.state === "ready" ? result.data.counts : null;
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title="Traders" />
      <Card>
        <Tabs
          value={segment}
          onChange={(_e, value: Segment) => {
            setParams((old) => {
              const next = new URLSearchParams(old);
              next.set("segment", value);
              return next;
            });
          }}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="Trader segments"
        >
          {SEGMENTS.map(([key, label]) => (
            <Tab key={key} value={key} label={counts ? `${label} (${counts[key]})` : label} />
          ))}
        </Tabs>
        <CardContent>
          <TextField
            label="Search"
            placeholder="CIF, name or email (an apprentice searches by CIF)"
            value={typed}
            onChange={(e) => {
              setTyped(e.target.value);
            }}
            size="small"
            fullWidth
            slotProps={{ htmlInput: { maxLength: 80 } }}
          />
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          {result.state === "loading" ? <p>Loading…</p> : null}
          {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
          {result.state === "ready" ? <TraderTable rows={result.data.traders} /> : null}
        </CardContent>
      </Card>
    </Stack>
  );
}

function TraderTable({ rows }: { rows: TraderRow[] }) {
  if (rows.length === 0) return <p>No traders here.</p>;
  return (
    <Table size="small" aria-label="traders">
      <TableHead>
        <TableRow>
          <TableCell>CIF</TableCell>
          <TableCell>Name</TableCell>
          <TableCell>Email</TableCell>
          <TableCell>Country</TableCell>
          <TableCell>Accounts</TableCell>
          <TableCell>Identity</TableCell>
          <TableCell>Paid out</TableCell>
          <TableCell>Registered</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((t) => (
          <TableRow key={t.cif} hover>
            <TableCell>
              <Link to={`/traders?cif=${t.cif}`}>{t.cif}</Link>
              {t.status === "blocked" ? <Chip size="small" color="error" label="blocked" sx={{ ml: 1 }} /> : null}
              {t.open_case ? <Chip size="small" color="warning" label="case" sx={{ ml: 1 }} /> : null}
            </TableCell>
            <TableCell>{t.full_name}</TableCell>
            <TableCell>{t.email}</TableCell>
            <TableCell>{t.residence_country}</TableCell>
            <TableCell>{t.funded > 0 ? `${t.accounts} (${t.funded} funded)` : t.accounts}</TableCell>
            <TableCell>{t.onboarding ?? "-"}</TableCell>
            <TableCell>{money(t.paid_cents)}</TableCell>
            <TableCell>{when(t.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** How each column of a file section is shown: money for *_cents, UTC
time for epoch fields, text otherwise. */
const TIMES = new Set(["created_at", "paid_at", "opened_at", "closed_at", "requested_at", "ts"]);
export function cell(key: string, value: string | number | null): string {
  if (value === null) return "-";
  if (typeof value === "number" && key.endsWith("_cents")) return money(value);
  if (typeof value === "number" && TIMES.has(key)) return when(value);
  return String(value);
}

function Section({ title, rows, empty }: { title: string; rows: Row[]; empty: string }) {
  const first = rows[0];
  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2", variant: "h6" } }} title={`${title} (${rows.length})`} />
      <CardContent>
        {first === undefined ? (
          <Typography color="text.secondary">{empty}</Typography>
        ) : (
          <Box sx={{ overflowX: "auto" }}>
            <Table size="small" aria-label={title}>
              <TableHead>
                <TableRow>
                  {Object.keys(first).map((k) => (
                    <TableCell key={k}>{k.replace(/_cents$/, "").replaceAll("_", " ")}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((r, i) => (
                  <TableRow key={i}>
                    {Object.entries(r).map(([k, v]) => (
                      <TableCell key={k}>{cell(k, v)}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

function TraderFilePage({ cif }: { cif: string }) {
  const { result } = useGet<TraderFile>(`/staff/traders/${cif}`);
  return (
    <Stack spacing={2} sx={{ mt: 2 }}>
      <Title title={`Trader ${cif}`} />
      <Box>
        <Button component={Link} to="/traders" size="small">
          Back to traders
        </Button>
      </Box>
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? <TraderFileView file={result.data} /> : null}
    </Stack>
  );
}

export function TraderFileView({ file }: { file: TraderFile }) {
  const t = file.trader;
  return (
    <>
      <Card>
        <CardHeader
          title={`${t.full_name} · ${t.cif}`}
          subheader={`${t.email} · ${t.residence_country} · registered ${when(t.created_at)}`}
        />
        <CardContent>
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            <Chip label={`Status: ${t.status}`} color={t.status === "blocked" ? "error" : "default"} />
            <Chip label={`Accounts: ${t.accounts}`} />
            <Chip label={`Funded: ${t.funded}`} />
            <Chip label={`Identity: ${t.onboarding ?? "not started"}`} />
            <Chip label={`Paid out: ${money(t.paid_cents)}`} />
            {t.open_case ? <Chip color="warning" label={`Open case ${t.open_case}`} /> : null}
          </Stack>
        </CardContent>
      </Card>
      <Section title="Accounts" rows={file.accounts} empty="No accounts." />
      <Section title="Purchases" rows={file.orders} empty="Nothing bought." />
      <Section title="Identity checks" rows={file.onboardings} empty="No identity checks." />
      <Section title="Cases" rows={file.cases} empty="No cases." />
      <Section title="Payouts" rows={file.payouts} empty="No payouts." />
      <Section title="Emails sent" rows={file.notifications} empty="No emails sent." />
      <Section title="Registration events" rows={file.events} empty="No events." />
    </>
  );
}
