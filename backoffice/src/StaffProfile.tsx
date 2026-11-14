import { Alert, Box, Button, Card, CardContent, CardHeader, Chip, ListItemIcon, ListItemText, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { forwardRef } from "react";
import { Logout, Title, UserMenu, useUserMenu } from "react-admin";
import { Link } from "react-router-dom";
import { config } from "./config";
import { ICONS, SIZE, STROKE } from "./icons";
import { useGet } from "./useGet";

interface StaffMe {
  access: { kind: string; tier: string | null; title: string | null; roles: string[]; sections: Record<string, string>; permissions: string[] };
  member: { sid: string; name: string; email: string; team: string | null; lead: string | null; starts_at: number; status: string } | null;
  grants: { grt: string; grant: string; value: string; status: string; ends_at: number | null }[];
}

/** The sign-in account page (password, authenticator, sessions) at the identity provider. */
export function accountUrl(authority: string = config.authority): string {
  return `${authority.replace(/\/+$/, "")}/account`;
}

function day(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** My profile: who I am in the back office, my access and grants, and
where to change my password and authenticator (owner remark, 14 Nov 2026). */
export function StaffProfilePage() {
  const { result } = useGet<StaffMe>("/staff/me");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4, maxWidth: 900 }}>
      <Title title="Profile" />
      {result.state === "loading" ? <p>Loading…</p> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" ? (
        <>
          <Card>
            <CardHeader slotProps={{ title: { component: "h2" } }} title={result.data.member?.name ?? "Staff member"} subheader={result.data.member?.email ?? ""} />
            <CardContent sx={{ pt: 0 }}>
              <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 3, rowGap: 1, m: 0 }}>
                <dt>Staff number</dt>
                <dd>{result.data.member?.sid ?? "-"}</dd>
                <dt>Title</dt>
                <dd>{result.data.access.title === null ? "-" : `${result.data.access.title} (${result.data.access.tier ?? ""})`}</dd>
                <dt>Team</dt>
                <dd>{result.data.member?.team ?? "-"}</dd>
                <dt>Since</dt>
                <dd>{result.data.member === null ? "-" : day(result.data.member.starts_at)}</dd>
                <dt>Roles</dt>
                <dd>
                  <Stack direction="row" sx={{ flexWrap: "wrap", gap: 0.75 }}>
                    {result.data.access.roles.map((r) => (
                      <Chip key={r} size="small" label={r} />
                    ))}
                  </Stack>
                </dd>
                <dt>Permissions</dt>
                <dd>{result.data.access.permissions.length.toString()}</dd>
              </Box>
            </CardContent>
          </Card>
          <Card>
            <CardHeader slotProps={{ title: { component: "h2" } }} title="Sign-in and security" subheader="Password, authenticator app and signed-in devices are managed at the sign-in service." />
            <CardContent sx={{ pt: 0 }}>
              <Button variant="outlined" href={accountUrl()} target="_blank" rel="noopener noreferrer">
                Manage password and authenticator
              </Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader slotProps={{ title: { component: "h2" } }} title="My grants" subheader="Tiers, acting roles and section powers given to me, with their dates. My work lists what waits for me." />
            <CardContent sx={{ pt: 0 }}>
              {result.data.grants.length === 0 ? (
                <Typography variant="body2">No grants beyond my roles.</Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Grant</TableCell>
                      <TableCell>Value</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Ends</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {result.data.grants.map((g) => (
                      <TableRow key={g.grt}>
                        <TableCell>{g.grant}</TableCell>
                        <TableCell>{g.value}</TableCell>
                        <TableCell>{g.status}</TableCell>
                        <TableCell>{g.ends_at === null ? "-" : day(g.ends_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              <Button component={Link} to="/" size="small" sx={{ mt: 1 }}>
                Open My work
              </Button>
            </CardContent>
          </Card>
        </>
      ) : null}
    </Stack>
  );
}

const ProfileItem = forwardRef<HTMLAnchorElement>(function ProfileItem(props, ref) {
  const menu = useUserMenu();
  return (
    <MenuItem ref={ref} component={Link} to="/profile" onClick={() => menu?.onClose()} {...props}>
      <ListItemIcon>
        <ICONS.traders size={SIZE} strokeWidth={STROKE} aria-hidden="true" />
      </ListItemIcon>
      <ListItemText>Profile</ListItemText>
    </MenuItem>
  );
});

/** The account button's menu: Profile, then sign out. */
export function StaffUserMenu() {
  return (
    <UserMenu>
      <ProfileItem />
      <Logout />
    </UserMenu>
  );
}
