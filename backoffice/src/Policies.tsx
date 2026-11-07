import { Alert, Button, Card, CardContent, CardHeader, Stack, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { when } from "./format";
import { useGet } from "./useGet";

interface PageSummary {
  slug: string;
  version: number;
  title: string;
  published_at: number;
}

interface PageText extends PageSummary {
  body: string;
}

const SLUG = /^[a-z0-9][a-z0-9-]{0,39}$/;

/** Policy pages (rule AC10): an owner proposes a new version here; the other
owner approves it under Setting proposals; traders accept it again. */
export function PolicyPages() {
  const { api } = useBackOffice();
  const { result, reload } = useGet<PageSummary[]>("/policies");
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = async (page: PageSummary): Promise<void> => {
    const text = await api.get<PageText>(`/policies/${page.slug}`);
    setSlug(text.slug);
    setTitle(text.title);
    setBody(text.body);
    setMessage(null);
  };

  const propose = async (): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/admin/policies/${slug}`, { title, body, reason });
      setMessage({ ok: true, text: "Proposed. The other owner approves it under Setting proposals." });
      setReason("");
      reload();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };

  const valid = SLUG.test(slug) && title.trim() !== "" && body.trim() !== "" && reason.trim() !== "";
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Policy pages" />
      <Card>
        <CardHeader slotProps={{ title: { component: "h2" } }} title="In force" />
        <CardContent sx={{ pt: 0 }}>
          {result.state === "loading" ? <p>Loading…</p> : null}
          {result.state === "error" ? <p role="alert">{result.message}</p> : null}
          {result.state === "ready" && result.data.length === 0 ? (
            <Typography color="text.secondary">No policy page is published yet.</Typography>
          ) : null}
          {result.state === "ready" ? (
            <Stack spacing={1}>
              {result.data.map((p) => (
                <Stack key={p.slug} direction="row" sx={{ alignItems: "center", gap: 1.5 }}>
                  <Typography sx={{ fontWeight: 600, flex: 1 }}>
                    {p.title} · version {p.version} · since {when(p.published_at)}
                  </Typography>
                  <Button size="small" onClick={() => void load(p)}>
                    Edit as new version
                  </Button>
                </Stack>
              ))}
            </Stack>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader
          slotProps={{ title: { component: "h2" } }}
          title="Propose a new version"
          subheader="Plain text. Traders must accept every new version before their next purchase."
        />
        <CardContent sx={{ pt: 0 }}>
          <Stack spacing={2}>
            <TextField
              label="Page address (e.g. terms, privacy, trade-policy)"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value.trim().toLowerCase());
              }}
              error={slug !== "" && !SLUG.test(slug)}
              slotProps={{ htmlInput: { maxLength: 40 } }}
            />
            <TextField label="Title" value={title} onChange={(e) => { setTitle(e.target.value); }} slotProps={{ htmlInput: { maxLength: 120 } }} />
            <TextField label="Text" value={body} onChange={(e) => { setBody(e.target.value); }} multiline minRows={12} slotProps={{ htmlInput: { maxLength: 100_000 } }} />
            <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
            {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
            <div>
              <Button variant="contained" disabled={!valid || busy} onClick={() => void propose()}>
                Propose
              </Button>
            </div>
          </Stack>
        </CardContent>
      </Card>
    </Stack>
  );
}
