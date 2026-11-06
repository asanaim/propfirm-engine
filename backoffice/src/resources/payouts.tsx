import { Stack } from "@mui/material";
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
import { MoneyField, TimeField } from "../fields";
import { History } from "./History";

const STATUSES = ["requested", "approved", "debited", "sent", "paid", "denied", "cancelled", "failed"];
const choices = (xs: string[]) => xs.map((x) => ({ id: x, name: x }));

const filters = [<SelectInput key="status" source="status" choices={choices(STATUSES)} alwaysOn />];

export function PayoutList() {
  return (
    <List filters={filters} filterDefaultValues={{ status: "requested" }} sort={{ field: "requested_at", order: "ASC" }}>
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="po" />
        <TextField source="acc" />
        <TextField source="cif" />
        <MoneyField source="trader_amount_cents" label="To the trader" />
        <TextField source="status" />
        <FunctionField label="Approvals" render={(r: RaRecord) => ((r.approvals as string[] | undefined) ?? []).length} />
        <TimeField source="requested_at" label="Requested" />
      </Datagrid>
    </List>
  );
}

/** Pass (T2+, payouts), then authorise (an admin, another person): the API
decides which step is due; this button sends "approve". */
function PayoutActions() {
  const record = useRecordContext();
  if (record?.status !== "requested") return null;
  const po = String(record.po);
  return (
    <Stack direction="row" spacing={1}>
      <ActionButton label="Approve (pass or authorise)" path={`/staff/payouts/${po}/approve`} />
      <ActionButton label="Deny" color="error" path={`/staff/payouts/${po}/deny`} />
    </Stack>
  );
}

export function PayoutShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <PayoutActions />
        <TextField source="po" />
        <TextField source="acc" />
        <TextField source="cif" />
        <TextField source="status" />
        <MoneyField source="trader_amount_cents" label="To the trader" />
        <MoneyField source="debit_cents" label="Debited from the account" />
        <FunctionField label="Approved by" render={(r: RaRecord) => ((r.approvals as string[] | undefined) ?? []).join(", ") || "-"} />
        <FunctionField
          label="Wallet"
          render={(r: RaRecord) => {
            const w = r.wallet as { network?: string; address?: string } | undefined;
            return w === undefined ? "-" : `${w.network ?? ""} ${w.address ?? ""}`;
          }}
        />
        <TimeField source="requested_at" label="Requested" />
        <TimeField source="paid_at" label="Paid" />
        <TextField source="provider_ref" emptyText="-" />
        <TextField source="failure" emptyText="-" />
        <History />
      </SimpleShowLayout>
    </Show>
  );
}
