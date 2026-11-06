import { Stack } from "@mui/material";
import {
  BooleanInput,
  Datagrid,
  List,
  SelectInput,
  Show,
  SimpleShowLayout,
  TextField,
  TextInput,
  useRecordContext,
} from "react-admin";
import { ActionButton } from "../ActionButton";
import { JsonField, TimeField } from "../fields";
import { History } from "./History";

const STATUSES = ["open", "decision_pending", "actioning", "closed"].map((x) => ({ id: x, name: x }));

const filters = [
  <SelectInput key="status" source="status" choices={STATUSES} alwaysOn />,
  <TextInput key="cif" source="cif" label="CIF" />,
  <BooleanInput key="overdue" source="overdue" label="Overdue only" />,
];

export function CaseList() {
  return (
    <List filters={filters} sort={{ field: "due_at", order: "ASC" }}>
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="cas" />
        <TextField source="cif" />
        <TextField source="severity" />
        <TextField source="status" />
        <TextField source="assignee" emptyText="-" />
        <TimeField source="due_at" label="Due" />
        <TimeField source="opened_at" label="Opened" />
      </Datagrid>
    </List>
  );
}

/** Work on a case: take it, note, decide; the second person confirms (a
block: T2+) or authorises (a void or profit removal: an admin) or sends the
decision back. Voids and profit removals name an account and an amount:
propose those through the API for now (POST /staff/cases/{cas}/decide). */
function CaseActions() {
  const record = useRecordContext();
  if (record === undefined) return null;
  const cas = String(record.cas);
  const base = `/staff/cases/${cas}`;
  if (record.status === "open") {
    return (
      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
        <ActionButton label="Take" path={`${base}/assign`} needsReason={false} />
        <ActionButton label="Add note" path={`${base}/notes`} field="text" fieldLabel="Note" maxLength={4000} />
        <ActionButton label="Clear" path={`${base}/decide`} extra={{ outcome: "cleared" }} />
        <ActionButton label="Warn" path={`${base}/decide`} extra={{ outcome: "warning" }} />
        <ActionButton
          label="Propose block"
          color="error"
          path={`${base}/decide`}
          extra={{ outcome: "actioned", actions: [{ action: "block_customer" }] }}
        />
      </Stack>
    );
  }
  if (record.status === "decision_pending") {
    return (
      <Stack direction="row" spacing={1}>
        <ActionButton label="Confirm (or authorise)" path={`${base}/confirm`} />
        <ActionButton label="Send back" color="error" path={`${base}/reject`} />
      </Stack>
    );
  }
  if (record.status === "closed") {
    return <ActionButton label="Reopen (new evidence)" path={`${base}/reopen`} />;
  }
  return null;
}

export function CaseShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <CaseActions />
        <TextField source="cas" />
        <TextField source="cif" />
        <TextField source="severity" />
        <TextField source="status" />
        <TextField source="assignee" emptyText="-" />
        <TextField source="outcome" emptyText="-" />
        <TextField source="proposed_by" emptyText="-" />
        <TimeField source="opened_at" label="Opened" />
        <TimeField source="due_at" label="Due" />
        <JsonField source="alerts" />
        <History />
      </SimpleShowLayout>
    </Show>
  );
}
