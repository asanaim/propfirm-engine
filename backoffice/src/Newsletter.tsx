import { Alert, Button, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";

type State = "proposed" | "approved" | "rejected" | "cancelled";

export interface Campaign {
  id: number;
  subject: string;
  body: string;
  created_by: string;
  created_at: number;
  state: State;
  decided_by: string | null;
  decided_at: number | null;
  reason: string | null;
  audience: number;
  sent: number;
  failed: number;
  skipped: number;
}

const COLOUR: Record<State, "warning" | "success" | "error" | "default"> = {
  proposed: "warning",
  approved: "success",
  rejected: "error",
  cancelled: "default",
};

/** Progress of an approved campaign in words. */
export function progress(c: Campaign): string {
  if (c.state !== "approved") return "";
  const done = c.sent + c.failed + c.skipped;
  const extra = [c.failed ? `${String(c.failed)} failed` : "", c.skipped ? `${String(c.skipped)} left the list` : ""].filter(Boolean).join(", ");
  return `${String(c.sent)} of ${String(c.audience)} sent${done < c.audience ? ", sending" : ""}${extra ? ` (${extra})` : ""}`;
}

/** Newsletter (D-15.2): one person proposes a campaign, another approves
it; it then goes to every confirmed subscriber, a batch a minute. */
export function NewsletterPage() {
  const { result, reload } = useGet<Campaign[]>("/staff/campaigns");
  const { api } = useBackOffice();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function act(path: string, payload: unknown, done: string) {
    setMessage(null);
    try {
      await api.post(path, payload);
      setMessage({ ok: true, text: done });
      reload();
      return true;
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The request failed." });
      return false;
    }
  }

  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Newsletter" />
      {message ? <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert> : null}
      <Card>
        <CardHeader title="New campaign" subheader="Plain text. Every email adds its own unsubscribe link and the firm's address. A second person approves before anything is sent." />
        <CardContent>
          <Stack spacing={2}>
            <TextField
              label="Subject"
              value={subject}
              onChange={(e) => {
                setSubject(e.target.value);
              }}
            />
            <TextField
              label="Text"
              multiline
              minRows={6}
              value={body}
              onChange={(e) => {
                setBody(e.target.value);
              }}
            />
            <div>
              <Button
                variant="contained"
                disabled={subject.trim().length < 3 || body.trim().length < 10}
                onClick={() => {
                  void act("/staff/campaigns", { subject, body }, "Proposed: a second person approves it.").then((ok) => {
                    if (ok) {
                      setSubject("");
                      setBody("");
                    }
                  });
                }}
              >
                Propose
              </Button>
            </div>
          </Stack>
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="Campaigns" />
        <CardContent>
          {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
          {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
          {result.state === "ready" && result.data.length === 0 ? <Typography color="text.secondary">No campaign yet.</Typography> : null}
          {result.state === "ready" && result.data.length > 0 ? (
            <Table size="small" aria-label="Campaigns">
              <TableHead>
                <TableRow>
                  <TableCell>Subject</TableCell>
                  <TableCell>State</TableCell>
                  <TableCell>Proposed</TableCell>
                  <TableCell>Progress</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {result.data.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell title={c.body}>{c.subject}</TableCell>
                    <TableCell>
                      <Chip size="small" color={COLOUR[c.state]} label={c.state} />
                    </TableCell>
                    <TableCell>
                      {c.created_by}, {when(c.created_at)}
                    </TableCell>
                    <TableCell>{c.state === "proposed" ? "" : c.state === "approved" ? progress(c) : `by ${c.decided_by ?? ""}`}</TableCell>
                    <TableCell>
                      {c.state === "proposed" ? (
                        <Stack direction="row" spacing={1}>
                          <Button
                            size="small"
                            onClick={() => {
                              void act(`/staff/campaigns/${String(c.id)}/decision`, { decision: "approve" }, "Approved: sending starts within a minute.");
                            }}
                          >
                            Approve
                          </Button>
                          <Button
                            size="small"
                            color="error"
                            onClick={() => {
                              void act(`/staff/campaigns/${String(c.id)}/decision`, { decision: "reject" }, "Rejected.");
                            }}
                          >
                            Reject
                          </Button>
                          <Button
                            size="small"
                            color="inherit"
                            onClick={() => {
                              void act(`/staff/campaigns/${String(c.id)}/decision`, { decision: "cancel" }, "Cancelled.");
                            }}
                          >
                            Cancel
                          </Button>
                        </Stack>
                      ) : null}
                    </TableCell>
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
