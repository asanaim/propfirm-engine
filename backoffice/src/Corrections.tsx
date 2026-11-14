import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

/** A price as text at the symbol's digits, in points: "1.10300" at 5 -> 110300; null when malformed. */
export function toPoints(text: string, digits: number): number | null {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (m === null) return null;
  const frac = m[2] ?? "";
  if (frac.length > digits) return null;
  return Number(`${m[1]}${frac.padEnd(digits, "0")}`);
}

export function fromPoints(points: number, digits: number): string {
  const s = points.toString().padStart(digits + 1, "0");
  return digits === 0 ? s : `${s.slice(0, -digits)}.${s.slice(-digits)}`;
}

export function cents(c: number): string {
  const sign = c < 0 ? "-" : c > 0 ? "+" : "";
  return `${sign}$${(Math.abs(c) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

interface Item {
  account_id: string;
  amount_cents: number;
  reinstate: boolean;
  fills: number;
  manual: string[];
}

interface Correction {
  cor: string;
  symbol: string;
  price_seq: number;
  bad_price: number;
  good_price: number;
  reason: string;
  status: "proposed" | "applied" | "rejected";
  proposed_by: string;
  proposed_at: number;
  decided_by: string | null;
  items: Item[];
  outcome: { input: string; account_id: string; status: string }[] | null;
}

interface Instrument {
  symbol: string;
  digits: number;
}

function Items({ items }: { items: readonly Item[] }) {
  if (items.length === 0) return <Typography variant="body2">No account was filled at this price.</Typography>;
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>Account</TableCell>
          <TableCell>Fills</TableCell>
          <TableCell align="right">Balance correction</TableCell>
          <TableCell>Breach undone</TableCell>
          <TableCell>For staff by hand</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {items.map((i) => (
          <TableRow key={i.account_id}>
            <TableCell>{i.account_id}</TableCell>
            <TableCell>{i.fills}</TableCell>
            <TableCell align="right">{cents(i.amount_cents)}</TableCell>
            <TableCell>{i.reinstate ? "Yes" : "No"}</TableCell>
            <TableCell>{i.manual.length === 0 ? "-" : i.manual.join("; ")}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

interface Fill {
  type: string;
  ts: number;
  symbol: string;
  price_seq: number;
  open_price?: number;
  close_price?: number;
  price?: number;
}

/** An account's fills, newest first, with the price number each used. */
export function fillsOf(events: readonly Fill[]): Fill[] {
  return events.filter((e) => (e.type === "PositionOpened" || e.type === "PositionClosed") && typeof e.price_seq === "number").reverse();
}

/** Find a bad update's price number from an account's trade history. */
function PriceNumberFinder({ onPick }: { onPick: (symbol: string, seq: number) => void }) {
  const { api } = useBackOffice();
  const [acc, setAcc] = useState("");
  const [fills, setFills] = useState<Fill[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const find = async () => {
    setError(null);
    try {
      setFills(fillsOf(await api.get<Fill[]>(`/accounts/${encodeURIComponent(acc.trim())}/history?limit=1000`)).slice(0, 30));
    } catch (exc) {
      setFills(null);
      setError(exc instanceof ApiError ? exc.message : "The history could not be read.");
    }
  };
  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title="Find the price number" subheader="An affected account's latest fills with the price number each used; pick one to fill the form." />
      <CardContent sx={{ pt: 0 }}>
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <TextField size="small" label="Account" value={acc} onChange={(e) => { setAcc(e.target.value); }} placeholder="ACC00000001" />
          <Button disabled={acc.trim() === ""} onClick={() => void find()}>
            Show fills
          </Button>
        </Stack>
        {error === null ? null : <Alert severity="error">{error}</Alert>}
        {fills === null ? null : fills.length === 0 ? (
          <Typography variant="body2">No fills on this account.</Typography>
        ) : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time (UTC)</TableCell>
                <TableCell>Fill</TableCell>
                <TableCell>Symbol</TableCell>
                <TableCell>Price number</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {fills.map((f, i) => (
                <TableRow key={`${f.ts.toString()}-${i.toString()}`}>
                  <TableCell>{new Date(f.ts).toISOString().slice(0, 19).replace("T", " ")}</TableCell>
                  <TableCell>{f.type === "PositionOpened" ? "Open" : "Close"}</TableCell>
                  <TableCell>{f.symbol}</TableCell>
                  <TableCell>{f.price_seq}</TableCell>
                  <TableCell>
                    <Button size="small" onClick={() => { onPick(f.symbol, f.price_seq); }}>
                      Use
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

/** Bad-price corrections (rule D-14.4): one person flags an applied price
and sees what reversing it does to each account; a second person approves,
and the engine corrects each balance and reinstates each account the bad
price breached. */
export function CorrectionsPage() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<Correction[]>("/staff/corrections");
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [symbol, setSymbol] = useState("EUR/USD");
  const [seq, setSeq] = useState("");
  const [good, setGood] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<{ bad_price: number; items: Item[] } | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<Instrument[]>("/tools/instruments").then(setInstruments, () => undefined);
  }, [api]);
  const digits = instruments.find((i) => i.symbol === symbol)?.digits ?? 5;
  const points = toPoints(good, digits);
  const priceSeq = /^\d+$/.test(seq) ? Number(seq) : null;
  const fail = (exc: unknown) => {
    setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
  };
  const look = async () => {
    if (points === null || priceSeq === null) return;
    setBusy(true);
    setMessage(null);
    try {
      const q = `symbol=${encodeURIComponent(symbol)}&price_seq=${priceSeq.toString()}&good_price=${points.toString()}`;
      setPreview(await api.get<{ bad_price: number; items: Item[] }>(`/staff/corrections/preview?${q}`));
    } catch (exc) {
      setPreview(null);
      fail(exc);
    } finally {
      setBusy(false);
    }
  };
  const propose = async () => {
    if (points === null || priceSeq === null) return;
    setBusy(true);
    try {
      const done = await api.post<Correction>("/staff/corrections", { symbol, price_seq: priceSeq, good_price: points, reason });
      setMessage({ ok: true, text: `${done.cor} proposed: a second person approves it below.` });
      setPreview(null);
      setSeq("");
      setGood("");
      setReason("");
      reload();
    } catch (exc) {
      fail(exc);
    } finally {
      setBusy(false);
    }
  };
  const decide = async (cor: string, how: "approve" | "reject") => {
    setBusy(true);
    try {
      const done = await api.post<Correction>(`/staff/corrections/${cor}/${how}`, {});
      setMessage({ ok: true, text: `${cor} ${done.status}.` });
      reload();
    } catch (exc) {
      fail(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Price corrections" />
      <PriceNumberFinder
        onPick={(s, n) => {
          setSymbol(s);
          setSeq(n.toString());
          setPreview(null);
        }}
      />
      <Card>
        <CardHeader
          slotProps={{ title: { component: "h2" } }}
          title="Flag a bad price"
          subheader="The symbol and price number of the bad update (shown in the trade history as the fill's price number), and the price it should have been. Positions it closed stay closed; their results are corrected."
        />
        <CardContent sx={{ pt: 0 }}>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                select
                label="Symbol"
                value={symbol}
                onChange={(e) => {
                  setSymbol(e.target.value);
                  setPreview(null);
                }}
                slotProps={{ select: { native: true } }}
                sx={{ minWidth: 160 }}
              >
                {(instruments.length === 0 ? [{ symbol: "EUR/USD", digits: 5 }] : instruments).map((i) => (
                  <option key={i.symbol} value={i.symbol}>
                    {i.symbol}
                  </option>
                ))}
              </TextField>
              <TextField label="Price number" value={seq} onChange={(e) => { setSeq(e.target.value); setPreview(null); }} />
              <TextField
                label="Good price"
                value={good}
                onChange={(e) => { setGood(e.target.value); setPreview(null); }}
                helperText={good !== "" && points === null ? `At most ${digits.toString()} decimals` : " "}
                error={good !== "" && points === null}
              />
            </Stack>
            <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
            <Stack direction="row" spacing={1}>
              <Button variant="outlined" disabled={busy || points === null || priceSeq === null} onClick={() => void look()}>
                Show the effect
              </Button>
              <Button variant="contained" disabled={busy || preview === null || reason.trim().length < 3} onClick={() => void propose()}>
                Propose
              </Button>
            </Stack>
            {preview === null ? null : (
              <>
                <Typography variant="body2">
                  Applied price {fromPoints(preview.bad_price, digits)}; corrected to {points === null ? "" : fromPoints(points, digits)}.
                </Typography>
                <Items items={preview.items} />
              </>
            )}
            {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          </Stack>
        </CardContent>
      </Card>
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready"
        ? result.data.map((c) => {
            const d = instruments.find((i) => i.symbol === c.symbol)?.digits ?? 5;
            return (
              <Card key={c.cor}>
                <CardHeader
                  slotProps={{ title: { component: "h2" } }}
                  title={`${c.cor} · ${c.symbol} price ${c.price_seq.toString()}`}
                  subheader={`${fromPoints(c.bad_price, d)} corrected to ${fromPoints(c.good_price, d)} · ${c.reason} · proposed by ${c.proposed_by}${c.decided_by === null ? "" : `, ${c.status} by ${c.decided_by}`}`}
                  action={<Chip label={c.status} color={c.status === "applied" ? "success" : c.status === "rejected" ? "default" : "warning"} />}
                />
                <CardContent sx={{ pt: 0 }}>
                  <Items items={c.items} />
                  {c.status === "proposed" ? (
                    <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                      <Button variant="contained" disabled={busy} onClick={() => void decide(c.cor, "approve")}>
                        Approve and apply
                      </Button>
                      <Button disabled={busy} onClick={() => void decide(c.cor, "reject")}>
                        Reject
                      </Button>
                    </Stack>
                  ) : null}
                  {c.outcome === null ? null : (
                    <Typography variant="body2" sx={{ mt: 1 }}>
                      {c.outcome.map((o) => `${o.input} ${o.account_id}: ${o.status}`).join(" · ")}
                    </Typography>
                  )}
                </CardContent>
              </Card>
            );
          })
        : null}
    </Stack>
  );
}
