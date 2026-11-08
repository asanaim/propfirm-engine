import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Title } from "react-admin";
import { ApiError } from "./api";
import { useBackOffice } from "./context";
import { useGet } from "./useGet";

interface Shape {
  fields: Record<string, number>;
  items: Record<string, number>;
  max_items: number;
  optional?: string[];
  images?: string[];
}
export interface SiteMode {
  maintenance: boolean;
  registration_open: boolean;
}
interface Section {
  section: string;
  fields: Record<string, string>;
  items: Record<string, string>[];
}
export interface SiteEditor {
  shapes: Record<string, Shape>;
  sections: Record<string, Section>;
  tokens: Record<string, string>;
  mode: SiteMode;
}

const TITLES: Record<string, string> = {
  hero: "Top of the home page",
  challenges: "Challenges",
  how: "How it works",
  maintenance: "Maintenance page",
  registration_closed: "Registration closed page",
};
const TOKEN = /\{([a-z_]+)\}/g;

/** The text with each {token} filled as the site will show it. */
export function preview(text: string, tokens: Record<string, string>): string {
  return text.replace(TOKEN, (whole, name: string) => tokens[name] ?? whole);
}

function words(key: string): string {
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function SectionEditor({ name, shape, data, onDone }: { name: string; shape: Shape; data: SiteEditor; onDone: () => void }) {
  const { api } = useBackOffice();
  const saved = data.sections[name];
  const blank = (keys: Record<string, number>) => Object.fromEntries(Object.keys(keys).map((k) => [k, ""]));
  const [fields, setFields] = useState<Record<string, string>>(saved?.fields ?? blank(shape.fields));
  const [items, setItems] = useState<Record<string, string>[]>(saved?.items ?? []);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const propose = async (): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/admin/site/${name}`, { fields, items, reason });
      setMessage({ ok: true, text: "Proposed. A second admin or owner approves it under Setting proposals." });
      setReason("");
      onDone();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };

  const isImage = (key: string) => shape.images?.includes(key) ?? false;
  const field = (key: string, value: string, longest: number, set: (v: string) => void) => (
    <TextField
      key={key}
      label={words(key)}
      value={value}
      onChange={(e) => {
        set(e.target.value);
      }}
      helperText={
        isImage(key)
          ? "Optional: the path of an image on the site, such as /brand/closed.svg"
          : value.includes("{")
            ? `Shows: ${preview(value, data.tokens)}`
            : `${value.length.toString()}/${longest.toString()}`
      }
      slotProps={{ htmlInput: { maxLength: longest } }}
      multiline={longest > 100}
      fullWidth
    />
  );

  return (
    <Card>
      <CardHeader slotProps={{ title: { component: "h2" } }} title={TITLES[name] ?? words(name)} subheader={saved === undefined ? "The site's own words are shown until text is approved here." : "Approved text in force."} />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={2}>
          {Object.entries(shape.fields).map(([k, longest]) =>
            field(k, fields[k] ?? "", longest, (v) => {
              setFields({ ...fields, [k]: v });
            }),
          )}
          {shape.max_items > 0 ? (
            <Stack spacing={1.5}>
              <Typography sx={{ fontWeight: 600 }}>
                Items ({items.length.toString()} of {shape.max_items.toString()})
              </Typography>
              {items.map((item, i) => (
                <Box key={i} sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 2 }}>
                  <Stack spacing={1.5}>
                    {Object.entries(shape.items).map(([k, longest]) =>
                      field(k, item[k] ?? "", longest, (v) => {
                        setItems(items.map((it, j) => (j === i ? { ...it, [k]: v } : it)));
                      }),
                    )}
                    <div>
                      <Button size="small" color="inherit" onClick={() => { setItems(items.filter((_, j) => j !== i)); }}>
                        Remove item
                      </Button>
                    </div>
                  </Stack>
                </Box>
              ))}
              {items.length < shape.max_items ? (
                <div>
                  <Button size="small" onClick={() => { setItems([...items, blank(shape.items)]); }}>
                    Add item
                  </Button>
                </div>
              ) : null}
            </Stack>
          ) : null}
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
          {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          <div>
            <Button variant="contained" disabled={busy || reason.trim() === ""} onClick={() => void propose()}>
              Propose
            </Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}

/** Maintenance and registration (rules AC16, AC17): a proposal a second person approves. */
function ModeEditor({ mode, onDone }: { mode: SiteMode; onDone: () => void }) {
  const { api } = useBackOffice();
  const [maintenance, setMaintenance] = useState(mode.maintenance);
  const [open, setOpen] = useState(mode.registration_open);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const same = maintenance === mode.maintenance && open === mode.registration_open;
  const propose = async (): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      await api.put("/admin/site-mode", { maintenance, registration_open: open, reason });
      setMessage({ ok: true, text: "Proposed. A second admin or owner approves it under Setting proposals." });
      setReason("");
      onDone();
    } catch (exc) {
      setMessage({ ok: false, text: exc instanceof ApiError ? exc.message : "The proposal failed." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader
        slotProps={{ title: { component: "h2" } }}
        title="Maintenance and registration"
        subheader={`In force: ${mode.maintenance ? "under maintenance" : "open"}, registration ${mode.registration_open ? "open" : "closed"}. Maintenance stops new registrations and challenge sales; sign in, trading and payouts carry on.`}
      />
      <CardContent sx={{ pt: 0 }}>
        <Stack spacing={1.5}>
          <FormControlLabel control={<Switch checked={maintenance} onChange={(e) => { setMaintenance(e.target.checked); }} />} label="Maintenance mode" />
          <FormControlLabel control={<Switch checked={open} onChange={(e) => { setOpen(e.target.checked); }} />} label="Registration open" />
          <TextField label="Reason (kept with the record)" value={reason} onChange={(e) => { setReason(e.target.value); }} slotProps={{ htmlInput: { maxLength: 500 } }} />
          {message === null ? null : <Alert severity={message.ok ? "success" : "error"}>{message.text}</Alert>}
          <div>
            <Button variant="contained" disabled={busy || same || reason.trim() === ""} onClick={() => void propose()}>
              Propose
            </Button>
          </div>
        </Stack>
      </CardContent>
    </Card>
  );
}

/** The website's text (rules AC07, AC08): edit a section, see it filled,
propose it; a second person approves. Figures are tokens, never typed. */
export function SiteEditorPage() {
  const { result, reload } = useGet<SiteEditor>("/admin/site");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Website" />
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <p role="alert">{result.message}</p> : null}
      {result.state === "ready" ? (
        <>
          <ModeEditor key={JSON.stringify(result.data.mode)} mode={result.data.mode} onDone={reload} />
          <Card>
            <CardHeader slotProps={{ title: { component: "h2" } }} title="Figures" subheader="Write a figure as its token, for example {max_split}. It is filled from the products in force, so the site never disagrees with them. Typed amounts and percentages are refused." />
            <CardContent sx={{ pt: 0 }}>
              <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
                {Object.entries(result.data.tokens).map(([k, v]) => (
                  <Chip key={k} label={`{${k}} = ${v === "" ? "none" : v}`} variant="outlined" />
                ))}
              </Stack>
            </CardContent>
          </Card>
          {Object.entries(result.data.shapes).map(([name, shape]) => (
            <SectionEditor key={`${name}:${JSON.stringify(result.data.sections[name] ?? null)}`} name={name} shape={shape} data={result.data} onDone={reload} />
          ))}
        </>
      ) : null}
    </Stack>
  );
}
