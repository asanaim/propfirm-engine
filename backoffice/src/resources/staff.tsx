import { Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import {
  Datagrid,
  FunctionField,
  List,
  SelectInput,
  Show,
  SimpleShowLayout,
  TextField,
  useRecordContext,
  type RaRecord,
} from "react-admin";
import { ActionButton } from "../ActionButton";
import { JsonField, TimeField } from "../fields";
import { when } from "../format";
import { useGet } from "../useGet";
import { History } from "./History";

const choices = (xs: string[]) => xs.map((x) => ({ id: x, name: x }));

/** The staff directory (T3+): members, their grants and history. */
export function MemberList() {
  return (
    <List filters={[<SelectInput key="status" source="status" choices={choices(["active", "left", "graduated"])} alwaysOn />]}>
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="sid" />
        <TextField source="name" />
        <TextField source="kind" />
        <TextField source="team" emptyText="-" />
        <TextField source="lead" emptyText="-" />
        <TextField source="mentor" emptyText="-" />
        <TextField source="status" />
        <TimeField source="starts_at" label="Started" />
        <TimeField source="ends_at" label="Ends" />
      </Datagrid>
    </List>
  );
}

interface GrantRow {
  grt: string;
  grant: string;
  value: string;
  status: string;
  in_force: boolean;
  requested_by: string;
  approved_by: string | null;
  starts_at: number;
  ends_at: number | null;
}

function Grants() {
  const record = useRecordContext();
  const grants = (record?.grants as GrantRow[] | undefined) ?? [];
  return (
    <Table size="small" aria-label="grants">
      <TableHead>
        <TableRow>
          <TableCell>Grant</TableCell>
          <TableCell>Value</TableCell>
          <TableCell>Status</TableCell>
          <TableCell>Asked by</TableCell>
          <TableCell>Approved by</TableCell>
          <TableCell>Ends</TableCell>
          <TableCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {grants.map((g) => (
          <TableRow key={g.grt}>
            <TableCell>{g.grant}</TableCell>
            <TableCell>{g.value}</TableCell>
            <TableCell>{g.in_force ? "in force" : g.status}</TableCell>
            <TableCell>{g.requested_by}</TableCell>
            <TableCell>{g.approved_by ?? "-"}</TableCell>
            <TableCell>{when(g.ends_at)}</TableCell>
            <TableCell>
              <GrantButtons grt={g.grt} status={g.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** A grant waiting: approve or deny (or cancel one's own request); in force:
revoke. Approving needs a fresh second factor. */
function GrantButtons({ grt, status }: { grt: string; status: string }) {
  const base = `/staff/grants/${grt}`;
  if (status === "requested") {
    return (
      <Stack direction="row" spacing={1}>
        <ActionButton label="Approve" path={`${base}/approve`} />
        <ActionButton label="Deny" color="error" path={`${base}/deny`} />
        <ActionButton label="Cancel" color="inherit" path={`${base}/cancel`} />
      </Stack>
    );
  }
  if (status === "approved") return <ActionButton label="Revoke" color="error" path={`${base}/revoke`} />;
  return null;
}

function MemberActions() {
  const record = useRecordContext();
  if (record === undefined) return null;
  const sid = String(record.sid);
  const base = `/staff/members/${sid}`;
  return (
    <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
      {record.status === "active" ? (
        <>
          <ActionButton label="Access review: keep" path={`${base}/review`} field="note" fieldLabel="Note" extra={{ decision: "keep" }} />
          <ActionButton label="Access review: change" path={`${base}/review`} field="note" fieldLabel="What changes" extra={{ decision: "change" }} />
          <ActionButton label="Leaver (ends access now)" color="error" path={`${base}/leave`} />
        </>
      ) : null}
      {record.status === "dormant" ? <ActionButton label="Reinstate" path={`${base}/reinstate`} /> : null}
      {record.kind === "apprentice" && record.status === "active" ? (
        <ActionButton label="Recommend for graduation" path={`${base}/recommend`} />
      ) : null}
    </Stack>
  );
}

interface ScreeningCheck {
  id: string;
  title: string;
  text: string;
  result: "pass" | "fail" | null;
  reference: string | null;
  recorded_by: string | null;
  recorded_at: number | null;
}
interface ScreeningView {
  status: "approved" | "pending" | "incomplete" | "expired" | "rejected";
  valid_until: number | null;
  missing: string[];
  checks: ScreeningCheck[];
}

const SCREENING_STATUS: Record<ScreeningView["status"], string> = {
  approved: "Approved",
  pending: "Every check passed: waiting for an admin",
  incomplete: "Incomplete: no staff access until approved",
  expired: "Expired: no staff access until rescreened",
  rejected: "Rejected",
};

/** Pre-employment screening (Restricted, T3+): a lead records each check
with a reference (never the document); an admin approves the whole round. */
function Screening() {
  const record = useRecordContext();
  const sid = String(record?.sid ?? "");
  const { result, reload } = useGet<ScreeningView>(`/staff/members/${sid}/screening`);
  if (result.state === "loading") return <p>Loading screening…</p>;
  if (result.state === "error") return <p>Screening: {result.message}</p>;
  const sc = result.data;
  const base = `/staff/members/${sid}/screening`;
  return (
    <section aria-label="Pre-employment screening">
      <h3 style={{ margin: "16px 0 8px" }}>Pre-employment screening</h3>
      <p>
        {SCREENING_STATUS[sc.status]}
        {sc.valid_until === null ? "" : ` (valid until ${when(sc.valid_until)})`}
      </p>
      <Table size="small" aria-label="screening checks">
        <TableHead>
          <TableRow>
            <TableCell>Check</TableCell>
            <TableCell>Result</TableCell>
            <TableCell>Reference</TableCell>
            <TableCell>Recorded</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {sc.checks.map((c) => (
            <TableRow key={c.id}>
              <TableCell title={c.text}>{c.title}</TableCell>
              <TableCell>{c.result ?? "-"}</TableCell>
              <TableCell>{c.reference ?? "-"}</TableCell>
              <TableCell>{c.recorded_at === null ? "-" : `${when(c.recorded_at)} by ${c.recorded_by ?? ""}`}</TableCell>
              <TableCell>
                <Stack direction="row" spacing={1}>
                  <ActionButton label="Pass" path={`${base}/checks`} field="reference" fieldLabel="Reference (never the document)" extra={{ check: c.id, result: "pass" }} onDone={reload} />
                  <ActionButton label="Fail" color="error" path={`${base}/checks`} field="reference" fieldLabel="Reference and finding" extra={{ check: c.id, result: "fail" }} onDone={reload} />
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {sc.status === "pending" ? (
        <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
          <ActionButton label="Approve screening" path={`${base}/decide`} field="note" fieldLabel="Note" extra={{ decision: "approved" }} onDone={reload} />
          <ActionButton label="Reject screening" color="error" path={`${base}/decide`} field="note" fieldLabel="Why" extra={{ decision: "rejected" }} onDone={reload} />
        </Stack>
      ) : null}
    </section>
  );
}

export function MemberShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <MemberActions />
        <TextField source="sid" />
        <TextField source="name" />
        <TextField source="email" emptyText="-" />
        <TextField source="kind" />
        <TextField source="status" />
        <TextField source="team" emptyText="-" />
        <TextField source="lead" emptyText="-" />
        <TextField source="mentor" emptyText="-" />
        <TextField source="recommended_by" emptyText="-" />
        <TimeField source="starts_at" label="Started" />
        <TimeField source="ends_at" label="Ends" />
        <Grants />
        <Screening />
        <History />
      </SimpleShowLayout>
    </Show>
  );
}

/** Grants waiting (or any status): the approver's queue. */
export function GrantList() {
  const statuses = ["requested", "approved", "denied", "cancelled", "revoked", "superseded"];
  return (
    <List
      filters={[<SelectInput key="status" source="status" choices={choices(statuses)} alwaysOn />]}
      filterDefaultValues={{ status: "requested" }}
      sort={{ field: "requested_at", order: "ASC" }}
    >
      <Datagrid bulkActionButtons={false}>
        <TextField source="grt" />
        <TextField source="sid" />
        <TextField source="grant" />
        <TextField source="value" />
        <TextField source="reason" />
        <TextField source="requested_by" label="Asked by" />
        <TimeField source="requested_at" label="Asked" />
        <FunctionField label="" render={(r: RaRecord) => <GrantButtons grt={String(r.grt)} status={String(r.status)} />} />
      </Datagrid>
    </List>
  );
}

/** Leavers' logins: disabled at the identity provider at once; a manual
disable (T3+) is confirmed here. */
export function CutoffList() {
  return (
    <List sort={{ field: "requested_at", order: "DESC" }}>
      <Datagrid bulkActionButtons={false}>
        <TextField source="sid" />
        <TextField source="subject" label="Login" />
        <TextField source="status" />
        <TextField source="how" emptyText="-" />
        <TextField source="last_error" emptyText="-" />
        <TimeField source="requested_at" label="Left" />
        <TimeField source="done_at" label="Done" />
        <FunctionField
          label=""
          render={(r: RaRecord) =>
            r.status === "done" ? null : <ActionButton label="Confirm disabled" path={`/staff/cutoffs/${String(r.sid)}/done`} />
          }
        />
      </Datagrid>
    </List>
  );
}

/** An apprentice's proposals: the mentor approves (carried out in the
mentor's name) or rejects. */
export function ApprenticeProposalList() {
  return (
    <List sort={{ field: "proposed_at", order: "DESC" }}>
      <Datagrid bulkActionButtons={false} expand={<JsonField source="body" />}>
        <TextField source="id" />
        <TextField source="sid" label="Apprentice" />
        <TextField source="action" />
        <TextField source="target" />
        <TextField source="status" />
        <TextField source="decided_by" emptyText="-" />
        <TimeField source="proposed_at" label="Proposed" />
        <FunctionField
          label=""
          render={(r: RaRecord) =>
            r.status !== "open" ? null : (
              <Stack direction="row" spacing={1}>
                <ActionButton label="Approve" path={`/staff/apprentice/proposals/${String(r.id)}/approve`} />
                <ActionButton label="Reject" color="error" path={`/staff/apprentice/proposals/${String(r.id)}/reject`} />
              </Stack>
            )
          }
        />
      </Datagrid>
    </List>
  );
}
