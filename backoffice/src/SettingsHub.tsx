import { Alert, Box, Button, Card, CardActions, CardContent, Chip, InputAdornment, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { Link, Title, usePermissions } from "react-admin";
import { config } from "./config";
import { ICONS, SIZE, STROKE } from "./icons";
import { type Area, canOpen, filterAreas } from "./places";
import { screensFor } from "./screens";
import { useGet } from "./useGet";

function AreaCard({ area, screens }: { area: Area; screens: ReadonlySet<string> }) {
  const Glyph = ICONS[area.icon];
  const open = canOpen(area, screens);
  return (
    <Card variant="outlined" sx={{ display: "flex", flexDirection: "column" }} data-testid={`area-${area.key}`}>
      <CardContent sx={{ flexGrow: 1 }}>
        <Stack direction="row" spacing={1.25} sx={{ mb: 1, alignItems: "center" }}>
          <Glyph size={SIZE} strokeWidth={STROKE} aria-hidden="true" />
          <Typography variant="subtitle1" component="h2">
            {area.title}
          </Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          {area.explains}
        </Typography>
      </CardContent>
      <CardActions>
        {open && area.path !== null ? (
          <Button component={Link} to={area.path} size="small">
            Open
          </Button>
        ) : area.state === "fixed" ? (
          <Chip size="small" label="Fixed in deployment" />
        ) : area.state === "planned" ? (
          <Chip size="small" variant="outlined" label={`Not built yet (${area.rule ?? "planned"})`} />
        ) : (
          <Chip size="small" variant="outlined" label="Your role cannot open this" />
        )}
      </CardActions>
    </Card>
  );
}

/** The System settings landing page (rule AC05): one card per area, with its own search. */
export function SettingsHub() {
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  const [query, setQuery] = useState("");
  const shown = filterAreas(query);
  return (
    <Box sx={{ p: 2 }}>
      <Title title="System settings" />
      <TextField
        label="Search settings"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
        }}
        size="small"
        sx={{ mb: 2, minWidth: 320 }}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <ICONS.search size={18} strokeWidth={STROKE} aria-hidden="true" />
              </InputAdornment>
            ),
          },
        }}
      />
      {shown.length === 0 ? <Alert severity="info">No settings area matches “{query}”.</Alert> : null}
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
        {shown.map((a) => (
          <AreaCard key={a.key} area={a} screens={screens} />
        ))}
      </Box>
    </Box>
  );
}

interface Basic {
  name: string;
  value: string;
  explains: string;
  why_fixed: string;
}

/** The firm-wide basics behind the General card (rule AC05), read only. */
export function GeneralPage() {
  const { result } = useGet<Basic[]>("/admin/general");
  const site: Basic = {
    name: "Site name",
    value: config.brand,
    explains: "The firm's name in the portal, the back office, emails and sign-in pages.",
    why_fixed: "Set when the firm is deployed, with the logo; a new name needs new artwork too.",
  };
  return (
    <Box sx={{ p: 2 }}>
      <Title title="General" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        The basics every account, price and record depends on. They are set in the deployment, not here, so a slip in the console can never change them for running accounts.
      </Typography>
      {result.state === "loading" ? <Typography>Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Setting</TableCell>
              <TableCell>Value</TableCell>
              <TableCell>What it means</TableCell>
              <TableCell>Why it is fixed</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {[site, ...result.data].map((b) => (
              <TableRow key={b.name}>
                <TableCell sx={{ fontWeight: 600 }}>{b.name}</TableCell>
                <TableCell>{b.value}</TableCell>
                <TableCell>{b.explains}</TableCell>
                <TableCell>{b.why_fixed}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </Box>
  );
}

export interface SecuritySwitch {
  name: string;
  value: boolean;
  locked: boolean;
  explains: string;
}

/** The security switches (rule AC16): read only, each with its effect. */
export function SecurityPage() {
  const { result } = useGet<SecuritySwitch[]>("/admin/security");
  return (
    <Box sx={{ p: 2 }}>
      <Title title="Security" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        These switches are locked in the policy file. The engine refuses a policy that turns one off, so they cannot be changed from the console.
      </Typography>
      {result.state === "loading" ? <Typography>Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Switch</TableCell>
              <TableCell>State</TableCell>
              <TableCell>What it does</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {result.data.map((s) => (
              <TableRow key={s.name}>
                <TableCell>
                  <code>{s.name}</code>
                </TableCell>
                <TableCell>
                  <Chip size="small" label={`${s.value ? "On" : "Off"}${s.locked ? ", locked" : ""}`} />
                </TableCell>
                <TableCell>{s.explains}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </Box>
  );
}
