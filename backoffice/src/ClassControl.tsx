import { Alert, Button, Chip, MenuItem, Stack, TextField } from "@mui/material";
import { useState } from "react";
import { useRecordContext, useRefresh } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";

export interface RiskClass {
  class: "A" | "B" | "C" | "D";
  reason: string;
  since: number;
}

/** The words for a class: C and D have every payout reviewed (D-14.5). */
export function classText(c: RiskClass | null | undefined): string {
  if (c === null || c === undefined) return "Not classed yet";
  const review = c.class === "C" || c.class === "D" ? ", every payout reviewed" : "";
  return `Class ${c.class}${review} (${c.reason})`;
}

/** The account's trader class on a payout, and a way to change it with a reason. */
export function ClassControl() {
  const record = useRecordContext<{ acc: string; risk_class: RiskClass | null }>();
  const { api } = useBackOffice();
  const refresh = useRefresh();
  const [cls, setCls] = useState<string>("B");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (record === undefined) return null;
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.put(`/staff/accounts/${record.acc}/class`, { class: cls, reason });
      setReason("");
      refresh();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "The class was not changed.");
    } finally {
      setBusy(false);
    }
  };
  const flagged = record.risk_class?.class === "C" || record.risk_class?.class === "D";
  return (
    <Stack spacing={1}>
      <Chip color={flagged ? "warning" : "default"} label={classText(record.risk_class)} sx={{ alignSelf: "flex-start" }} />
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField select size="small" label="Class" value={cls} onChange={(e) => { setCls(e.target.value); }} sx={{ minWidth: 100 }}>
          {["A", "B", "C", "D"].map((c) => (
            <MenuItem key={c} value={c}>
              {c}
            </MenuItem>
          ))}
        </TextField>
        <TextField size="small" label="Reason" value={reason} onChange={(e) => { setReason(e.target.value); }} sx={{ minWidth: 260 }} />
        <Button disabled={busy || reason.trim().length < 3} onClick={() => void save()}>
          Set class
        </Button>
      </Stack>
      {error === null ? null : <Alert severity="error">{error}</Alert>}
    </Stack>
  );
}
