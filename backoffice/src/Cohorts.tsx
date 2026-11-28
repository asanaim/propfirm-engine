import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";

interface CohortRow {
  kind: "product" | "affiliate";
  key: string;
  product_id: string;
  resolved: number;
  passed: number;
  rate: string;
  bound: string;
  above: boolean;
  alarmed: boolean;
}

interface AlarmRow {
  id: number;
  kind: "product" | "affiliate";
  key: string;
  product_id: string;
  resolved: number;
  passed: number;
  bound: string;
  raised_at: number;
  requested_at: number | null;
  requested_by: string | null;
  request_reason: string | null;
  cleared_at: number | null;
  cleared_by: string | null;
  reason: string | null;
}

export interface CohortDesk {
  significance: string;
  min_resolved: number;
  cohorts: CohortRow[];
  alarms: AlarmRow[];
}

/** A cohort's name in words: "lite:2026-11" -> "lite, bought in 2026-11";
"CIF00000007:lite" -> "affiliate CIF00000007, lite". */
export function cohortName(row: Pick<CohortRow, "kind" | "key">): string {
  const [a, b] = row.key.split(":");
  return row.kind === "product" ? `${a ?? ""}, bought in ${b ?? ""}` : `affiliate ${a ?? ""}, ${b ?? ""}`;
}

/** Cohort alarm (D-14.33): pass rates against the zero edge bound; an alarm
holds the cohort's payouts (and pauses an affiliate's commission) until
cleared here. */
export function CohortsPage() {
  const { result, reload } = useGet<CohortDesk>("/staff/cohorts");
  const { api } = useBackOffice();
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function clear(id: number) {
    setMessage(null);
    try {
      const done = await api.post<{ status: "requested" | "cleared" }>(`/staff/cohorts/${String(id)}/clear`, { reason });
      setMessage({
        ok: true,
        text: done.status === "cleared" ? `Alarm ${String(id)} cleared.` : `Clearing alarm ${String(id)} asked; a second person confirms it.`,
      });
      setReason("");
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    }
  }

  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Cohort alarm" />
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      {result.state === "ready" ? (
        <>
          <Card>
            <CardHeader
              title="Alarms"
              subheader="An open alarm holds the cohort's payouts; for an affiliate it also pauses their commission. Two people clear it: one asks, another confirms."
            />
            <CardContent>
              {result.data.alarms.length === 0 ? (
                <Typography color="text.secondary">No alarm has been raised.</Typography>
              ) : (
                <>
                  <TextField
                    size="small"
                    label="Reason for clearing"
                    value={reason}
                    onChange={(e) => {
                      setReason(e.target.value);
                    }}
                    sx={{ mb: 2, minWidth: 360 }}
                  />
                  <Table size="small" aria-label="Cohort alarms">
                    <TableHead>
                      <TableRow>
                        <TableCell>Cohort</TableCell>
                        <TableCell>Passed</TableCell>
                        <TableCell>Raised</TableCell>
                        <TableCell>Status</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {result.data.alarms.map((a) => (
                        <TableRow key={a.id}>
                          <TableCell>{cohortName(a)}</TableCell>
                          <TableCell>
                            {a.passed} of {a.resolved}
                          </TableCell>
                          <TableCell>{when(a.raised_at)}</TableCell>
                          <TableCell>
                            {a.cleared_at === null ? (
                              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                                {a.requested_by === null ? null : (
                                  <Typography variant="body2" color="text.secondary">
                                    Asked by {a.requested_by}: {a.request_reason ?? ""}
                                  </Typography>
                                )}
                                <Button
                                  size="small"
                                  variant="outlined"
                                  disabled={reason.trim().length < 3}
                                  onClick={() => {
                                    void clear(a.id);
                                  }}
                                >
                                  {a.requested_by === null ? "Ask to clear" : "Confirm clear"}
                                </Button>
                              </Stack>
                            ) : (
                              `Cleared by ${a.requested_by ?? ""} and ${a.cleared_by ?? ""}: ${a.reason ?? ""}`
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader
              title="Cohorts"
              subheader={`An alarm needs ${String(result.data.min_resolved)} results and a rate above the bound at ${result.data.significance} significance.`}
            />
            <CardContent>
              {result.data.cohorts.length === 0 ? (
                <Typography color="text.secondary">No resolved challenge yet.</Typography>
              ) : (
                <Table size="small" aria-label="Cohorts">
                  <TableHead>
                    <TableRow>
                      <TableCell>Cohort</TableCell>
                      <TableCell align="right">Results</TableCell>
                      <TableCell align="right">Pass rate</TableCell>
                      <TableCell align="right">Zero edge bound</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.data.cohorts.map((c) => (
                      <TableRow key={`${c.kind}:${c.key}`}>
                        <TableCell>{cohortName(c)}</TableCell>
                        <TableCell align="right">{c.resolved}</TableCell>
                        <TableCell align="right">{c.rate}</TableCell>
                        <TableCell align="right">{c.bound}</TableCell>
                        <TableCell>
                          {c.alarmed ? <Chip size="small" color="error" label="Alarm" /> : c.above ? <Chip size="small" color="warning" label="Above, not significant" /> : null}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </Stack>
  );
}
