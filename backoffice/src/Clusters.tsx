import { Alert, Box, Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Link } from "react-router-dom";
import { Title } from "react-admin";
import { when } from "./format";
import { recordPath } from "./places";
import { useGet } from "./useGet";

interface ClusterLink {
  a_cif: string;
  b_cif: string;
  reason: "identity" | "wallet" | "trades";
  evidence: Record<string, unknown>;
  first_seen: number;
  last_seen: number;
}

interface Cluster {
  cluster: string;
  members: string[];
  links: ClusterLink[];
}

const REASON: Record<ClusterLink["reason"], string> = {
  identity: "Same verified person",
  wallet: "Same payout wallet",
  trades: "Matching trades",
};

/** What a link rests on, in words. */
export function evidenceText(link: ClusterLink): string {
  const e = link.evidence;
  if (link.reason === "wallet") return typeof e.address === "string" ? `address ${e.address}` : "a shared address";
  if (link.reason === "trades") {
    const symbols = Array.isArray(e.symbols) ? e.symbols.join(", ") : "";
    const matches = typeof e.matches === "number" ? e.matches : 0;
    return `${String(matches)} positions opened within 2 s of each other (${symbols}), last 30 days`;
  }
  return typeof e.onboarding === "string" ? `found at identity check ${e.onboarding}` : "found at an identity check";
}

/** Identity clusters (D-14.27): customers who are, or act as, one person.
A cluster counts as one trader for the reserve's largest claim, and later
for the allocation cap and Instant limit. */
export function ClustersPage() {
  const { result } = useGet<Cluster[]>("/staff/clusters");
  return (
    <Stack spacing={2.5} sx={{ mt: 2, mb: 4 }}>
      <Title title="Identity clusters" />
      {result.state === "loading" ? <Typography color="text.secondary">Loading…</Typography> : null}
      {result.state === "error" ? <Alert severity="error">{result.message}</Alert> : null}
      {result.state === "ready" && result.data.length === 0 ? (
        <Typography color="text.secondary">No customers are linked. Links are looked for every hour.</Typography>
      ) : null}
      {result.state === "ready"
        ? result.data.map((c) => (
            <Card key={c.cluster}>
              <CardHeader
                title={`Cluster ${c.cluster}`}
                subheader={`${String(c.members.length)} customers, counted as one trader`}
                action={
                  <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    {c.members.map((m) => (
                      <Chip key={m} size="small" label={m} component={Link} to={recordPath(m) ?? "/traders"} clickable />
                    ))}
                  </Box>
                }
              />
              <CardContent>
                <Table size="small" aria-label={`Links of ${c.cluster}`}>
                  <TableHead>
                    <TableRow>
                      <TableCell>Between</TableCell>
                      <TableCell>Why</TableCell>
                      <TableCell>Evidence</TableCell>
                      <TableCell>Last seen</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {c.links.map((l) => (
                      <TableRow key={`${l.a_cif}-${l.b_cif}-${l.reason}`}>
                        <TableCell sx={{ fontFamily: "monospace" }}>
                          {l.a_cif} and {l.b_cif}
                        </TableCell>
                        <TableCell>{REASON[l.reason]}</TableCell>
                        <TableCell>{evidenceText(l)}</TableCell>
                        <TableCell>{when(l.last_seen)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ))
        : null}
    </Stack>
  );
}
