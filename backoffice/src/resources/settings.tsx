import { Stack } from "@mui/material";
import { BooleanInput, Datagrid, FunctionField, List, TextField, type RaRecord } from "react-admin";
import { ActionButton } from "../ActionButton";
import { JsonField, TimeField } from "../fields";

/** Setting changes waiting for a second admin (maker-checker): approve
applies the change; the author may withdraw it. */
export function ProposalList() {
  return (
    <List
      filters={[<BooleanInput key="open" source="open" label="Open only" alwaysOn />]}
      filterDefaultValues={{ open: true }}
      sort={{ field: "created_at", order: "DESC" }}
    >
      <Datagrid bulkActionButtons={false} expand={<JsonField source="value" />}>
        <TextField source="id" />
        <TextField source="kind" />
        <TextField source="author" />
        <TextField source="reason" />
        <TextField source="status" />
        <TimeField source="created_at" label="Proposed" />
        <TimeField source="expires_at" label="Expires" />
        <FunctionField
          label=""
          render={(r: RaRecord) =>
            r.status !== "open" ? null : (
              <Stack direction="row" spacing={1}>
                <ActionButton label="Approve" path={`/admin/settings/proposals/${String(r.id)}/approve`} />
                <ActionButton label="Reject" color="error" path={`/admin/settings/proposals/${String(r.id)}/reject`} />
                <ActionButton label="Withdraw" color="inherit" path={`/admin/settings/proposals/${String(r.id)}/withdraw`} />
              </Stack>
            )
          }
        />
      </Datagrid>
    </List>
  );
}

/** Every setting change applied (or due), newest first. */
export function ChangeList() {
  return (
    <List sort={{ field: "created_at", order: "DESC" }}>
      <Datagrid bulkActionButtons={false} expand={<JsonField source="value" />}>
        <TextField source="id" />
        <TextField source="kind" />
        <TextField source="author" />
        <TextField source="approved_by" emptyText="-" />
        <TextField source="reason" />
        <TextField source="status" />
        <TimeField source="created_at" label="Made" />
        <TimeField source="applied_at" label="Applied" />
      </Datagrid>
    </List>
  );
}
