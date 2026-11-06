import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField } from "@mui/material";
import { useState } from "react";
import { useNotify, useRefresh } from "react-admin";
import { ApiError, StepUpRequired } from "./api";
import { stepUp } from "./auth";
import { useBackOffice } from "./context";

/** One action on one record, always with a reason (the record keeps it).
An approval may need the second factor again: the button then offers it. */
export function ActionButton({
  label,
  path,
  extra = {},
  color = "primary",
  needsReason = true,
  field = "reason",
  fieldLabel = "Reason (kept with the record)",
  maxLength = 500, // the API's Reason
}: {
  label: string;
  path: string;
  extra?: Record<string, unknown>;
  color?: "primary" | "error" | "inherit";
  needsReason?: boolean;
  field?: string;
  fieldLabel?: string;
  maxLength?: number;
}) {
  const { api, manager } = useBackOffice();
  const notify = useNotify();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [needsStepUp, setNeedsStepUp] = useState(false);

  const send = async (): Promise<void> => {
    setBusy(true);
    try {
      await api.post(path, needsReason ? { ...extra, [field]: reason.trim() } : extra);
      notify(`${label}: done`, { type: "success" });
      setOpen(false);
      setReason("");
      refresh();
    } catch (e) {
      if (e instanceof StepUpRequired) {
        setNeedsStepUp(true);
        setOpen(true);
      } else {
        notify(e instanceof ApiError ? e.message : `${label} failed`, { type: "error" });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        size="small"
        variant="contained"
        color={color}
        onClick={() => {
          setNeedsStepUp(false);
          if (needsReason) setOpen(true);
          else void send();
        }}
      >
        {label}
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        aria-labelledby={`${path}-title`}
      >
        <DialogTitle id={`${path}-title`}>{label}</DialogTitle>
        <DialogContent>
          {needsStepUp ? (
            <p>For an approval, confirm it is you with your authenticator code, then press the button again.</p>
          ) : (
            <TextField
              autoFocus
              fullWidth
              multiline
              minRows={2}
              margin="dense"
              label={fieldLabel}
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
              }}
              slotProps={{ htmlInput: { maxLength } }}
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setOpen(false);
            }}
          >
            Cancel
          </Button>
          {needsStepUp ? (
            <Button variant="contained" onClick={() => void stepUp(manager)}>
              Confirm it is you
            </Button>
          ) : (
            <Button variant="contained" color={color} disabled={busy || reason.trim() === ""} onClick={() => void send()}>
              {label}
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </>
  );
}
