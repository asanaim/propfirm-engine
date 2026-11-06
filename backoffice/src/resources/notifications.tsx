import { BooleanField, BooleanInput, Datagrid, FunctionField, List, TextField, type RaRecord } from "react-admin";
import { ActionButton } from "../ActionButton";
import { TimeField } from "../fields";

/** Notifications reaching me (by my permissions or to me by name). */
export function NotificationList() {
  return (
    <List filters={[<BooleanInput key="all" source="all" label="Include done" alwaysOn />]} sort={{ field: "ts", order: "DESC" }}>
      <Datagrid bulkActionButtons={false}>
        <BooleanField source="urgent" />
        <TextField source="kind" />
        <TextField source="ref" />
        <TextField source="title" />
        <TextField source="body" />
        <TimeField source="ts" label="When" />
        <FunctionField
          label=""
          render={(r: RaRecord) =>
            r.acknowledged_at !== null || r.closed_at !== null ? null : (
              <ActionButton label="Acknowledge" path={`/staff/notifications/${String(r.id)}/ack`} needsReason={false} />
            )
          }
        />
      </Datagrid>
    </List>
  );
}
