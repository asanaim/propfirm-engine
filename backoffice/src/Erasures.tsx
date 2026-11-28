import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";

export interface ErasureRow {
  id: number;
  cif: string;
  reason: string;
  proposed_by: string;
  proposed_at: number;
  status: "proposed" | "approved" | "rejected" | "cancelled";
  decided_by: string | null;
  decided_at: number | null;
  decision_reason: string | null;
  erased: Record<string, number> | null;
}

const COLOR = { proposed: "warning", approved: "success", rejected: "default", cancelled: "default" } as const;

/** The rows overwritten, in words; the identity login is removed by hand. */
export function summary(erased: Record<string, number>): string {
  const touched = Object.entries(erased)
    .filter(([k, n]) => k !== "identity_login" && n > 0)
    .map(([k, n]) => `${k.replace(/_/g, " ")} ${String(n)}`);
  return `${touched.join(", ") || "nothing"}. Remove the login in the identity provider by hand.`;
}

/** Data erasure (rule AC27, D-15.5): an owner proposes erasing a customer's
personal data, the other owner approves, and it runs at once. Refused while
the data must be kept: five years after the relationship ends, open money,
an open case. Records stay; only personal data is overwritten. */
export function ErasuresPage() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<ErasureRow[]>("/admin/erasures");
  const [cif, setCif] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function send(path: string, body: unknown, done: string) {
    setMessage(null);
    try {
      await api.post(path, body);
      setMessage({ ok: true, text: done });
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
    }
  }

  async function decide(row: ErasureRow, decision: "approve" | "reject" | "cancel") {
    const why = window.prompt(`Reason to ${decision} erasure ${String(row.id)} of ${row.cif}`);
    if (why === null || why.trim().length < 3) return;
    await send(`/admin/erasures/${String(row.id)}/decision`, { decision, reason: why.trim() }, `Erasure ${String(row.id)}: ${decision}d.`);
  }

  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Data erasure" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="Propose an erasure" subheader="Owners only. The other owner approves; the erasure runs at approval and cannot be undone." />
        <CardContent>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              label="Customer (CIF)"
              value={cif}
              onChange={(e) => {
                setCif(e.target.value.trim().toUpperCase());
              }}
            />
            <TextField
              label="Reason"
              value={reason}
              sx={{ flex: 1 }}
              onChange={(e) => {
                setReason(e.target.value);
              }}
            />
            <Button
              variant="contained"
              disabled={!/^CIF[0-9]{8}$/.test(cif) || reason.trim().length < 3}
              onClick={() => {
                void send("/admin/erasures", { cif, reason: reason.trim() }, "Proposed: the other owner decides it.").then(() => {
                  setReason("");
                });
              }}
            >
              Propose
            </Button>
          </Stack>
        </CardContent>
      </Card>
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? (
        <Card>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>No.</TableCell>
                <TableCell>Customer</TableCell>
                <TableCell>Proposed</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Outcome</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {result.data.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{row.id}</TableCell>
                  <TableCell>{row.cif}</TableCell>
                  <TableCell>
                    {when(row.proposed_at)} by {row.proposed_by}: {row.reason}
                  </TableCell>
                  <TableCell>
                    <Chip size="small" color={COLOR[row.status]} label={row.status} />
                  </TableCell>
                  <TableCell>{row.erased ? summary(row.erased) : (row.decision_reason ?? "")}</TableCell>
                  <TableCell>
                    {row.status === "proposed" ? (
                      <Stack direction="row" spacing={1}>
                        <Button size="small" color="error" onClick={() => void decide(row, "approve")}>
                          Approve and erase
                        </Button>
                        <Button size="small" onClick={() => void decide(row, "reject")}>
                          Reject
                        </Button>
                        <Button size="small" color="inherit" onClick={() => void decide(row, "cancel")}>
                          Cancel
                        </Button>
                      </Stack>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      ) : null}
    </Stack>
  );
}
