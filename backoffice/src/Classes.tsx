import { Alert, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Link } from "react-router-dom";
import { Title } from "react-admin";
import { when } from "./format";
import { recordPath } from "./places";
import { useGet } from "./useGet";

type TraderClass = "A" | "B" | "C" | "D";

interface ClassRow {
  acc: string;
  cif: string;
  class: TraderClass;
  reason: string;
  set_by: string;
  at: number;
}

interface ClassList {
  counts: Record<TraderClass, number>;
  accounts: ClassRow[];
}

const MEANING: Record<TraderClass, string> = {
  A: "Proven",
  B: "Standard",
  C: "Watch",
  D: "Restricted",
};

const WORDS: Record<string, string> = {
  proven: "proven record",
  standard: "standard",
  short_holds: "most profit from short holds",
  chargeback: "a chargeback",
  trades_cluster: "trades match another customer",
  case_actioned: "a case actioned",
};

/** Why a class was set, in words: the daily job's reasons, or the text a
person or the payout check gave. */
export function reasonText(row: Pick<ClassRow, "reason">): string {
  const daily = /^daily: ([^;]*);/.exec(row.reason)?.[1];
  if (daily === undefined) return row.reason;
  return daily
    .split(",")
    .map((r) => WORDS[r] ?? r)
    .join(", ");
}

/** Trader classes (D-14.5, D-14.28): the daily job classes every funded
account; a class set by a person stands until a newer fact. C and D: every
payout reviewed, no scaling, no Instant purchase. */
export function ClassesPage() {
  const { result } = useGet<ClassList>("/staff/classes");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Trader classes" />
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? (
        <Card>
          <CardHeader
            title="Classes in force"
            subheader="Accounts with no record are class B. Classed once a day."
            action={
              <Stack direction="row" spacing={0.5}>
                {(["A", "B", "C", "D"] as const).map((c) => (
                  <Chip key={c} size="small" label={`${c} ${MEANING[c]}: ${String(result.data.counts[c])}`} />
                ))}
              </Stack>
            }
          />
          <CardContent>
            {result.data.accounts.length === 0 ? (
              <Typography color="text.secondary">No account has a class recorded.</Typography>
            ) : (
              <Table size="small" aria-label="Classes in force">
                <TableHead>
                  <TableRow>
                    <TableCell>Account</TableCell>
                    <TableCell>Class</TableCell>
                    <TableCell>Why</TableCell>
                    <TableCell>Set by</TableCell>
                    <TableCell>Since</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.data.accounts.map((r) => (
                    <TableRow key={r.acc}>
                      <TableCell sx={{ fontFamily: "monospace" }}>
                        <Link to={recordPath(r.acc) ?? "/traders"}>{r.acc}</Link>
                      </TableCell>
                      <TableCell>
                        {r.class} {MEANING[r.class]}
                      </TableCell>
                      <TableCell>{reasonText(r)}</TableCell>
                      <TableCell>{r.set_by === "system" ? "Daily job or payout check" : r.set_by}</TableCell>
                      <TableCell>{when(r.at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : null}
    </Stack>
  );
}
