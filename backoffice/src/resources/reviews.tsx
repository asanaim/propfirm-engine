import { Stack } from "@mui/material";
import { Datagrid, List, SelectInput, Show, SimpleShowLayout, TextField, useRecordContext } from "react-admin";
import { ActionButton } from "../ActionButton";
import { JsonField, MoneyField, TimeField } from "../fields";
import { History } from "./History";

const choices = (xs: string[]) => xs.map((x) => ({ id: x, name: x }));
const PRG = ["provisioning", "provisioned", "failed", "review", "approved", "denied", "blocked"];
const ONB = [
  "verification",
  "verification_declined",
  "checks",
  "review",
  "rejected",
  "contract",
  "signature",
  "signed",
  "provisioned",
  "failed",
];

function Decide({ path, status }: { path: string; status: string }) {
  const record = useRecordContext();
  if (record?.status !== status) return null;
  const id = String(record.id);
  return (
    <Stack direction="row" spacing={1}>
      <ActionButton label="Approve" path={`${path}/${id}/approve`} />
      <ActionButton label="Deny" color="error" path={`${path}/${id}/deny`} />
    </Stack>
  );
}

/** Funded reviews (risk desk, T2+): a passed evaluation becomes funded. */
export function ProgressionList() {
  return (
    <List
      filters={[<SelectInput key="status" source="status" choices={choices(PRG)} alwaysOn />]}
      filterDefaultValues={{ status: "review" }}
      sort={{ field: "created_at", order: "ASC" }}
    >
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="prg" />
        <TextField source="cif" />
        <TextField source="product_id" label="Product" />
        <MoneyField source="size_cents" label="Size" />
        <TextField source="from_stage" label="From" />
        <TextField source="to_stage" label="To" />
        <TextField source="status" />
        <TimeField source="created_at" label="Since" />
      </Datagrid>
    </List>
  );
}

export function ProgressionShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <Decide path="/staff/progressions" status="review" />
        <TextField source="prg" />
        <TextField source="cif" />
        <TextField source="product_id" label="Product" />
        <MoneyField source="size_cents" label="Size" />
        <TextField source="from_acc" />
        <TextField source="from_stage" />
        <TextField source="to_stage" />
        <TextField source="status" />
        <TextField source="failure" emptyText="-" />
        <TextField source="decided_by" emptyText="-" />
        <TimeField source="passed_at" label="Passed" />
        <History />
      </SimpleShowLayout>
    </Show>
  );
}

/** Identity reviews (KYC, T1+): the verified identity differs from the record. */
export function OnboardingList() {
  return (
    <List
      filters={[<SelectInput key="status" source="status" choices={choices(ONB)} alwaysOn />]}
      filterDefaultValues={{ status: "review" }}
      sort={{ field: "created_at", order: "ASC" }}
    >
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="onb" />
        <TextField source="cif" />
        <TextField source="source" />
        <MoneyField source="size_cents" label="Size" />
        <TextField source="status" />
        <TimeField source="created_at" label="Since" />
      </Datagrid>
    </List>
  );
}

export function OnboardingShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <Decide path="/staff/onboardings" status="review" />
        <TextField source="onb" />
        <TextField source="cif" />
        <TextField source="source" />
        <TextField source="status" />
        <TextField source="acc" emptyText="-" />
        <TextField source="failure" emptyText="-" />
        <JsonField source="verified" label="Verified identity" />
        <History />
      </SimpleShowLayout>
    </Show>
  );
}
