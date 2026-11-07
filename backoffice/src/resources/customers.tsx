import { Card, CardContent, CardHeader, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import {
  Datagrid,
  FunctionField,
  List,
  type RaRecord,
  SelectInput,
  Show,
  SimpleShowLayout,
  TextField,
  TextInput,
  useRecordContext,
} from "react-admin";
import { Link } from "react-router-dom";
import { MoneyField, TimeField } from "../fields";
import { money, when } from "../format";

const choices = (xs: string[]) => xs.map((x) => ({ id: x, name: x }));
const STAGE_NAMES: Record<string, string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };
const STATUS_COLOUR: Record<string, "success" | "info" | "error" | "warning" | "default"> = {
  active: "info",
  passed: "success",
  breached: "error",
  voided: "warning",
  registered: "success",
  blocked: "error",
};

/** A status as a coloured chip. */
export function StatusChip({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <Chip size="small" label="not live yet" variant="outlined" />;
  return <Chip size="small" label={value} color={STATUS_COLOUR[value] ?? "default"} variant={value === "active" ? "outlined" : "filled"} />;
}

/** Registered customers (staff T1+; apprentices see personal data masked). */
export function CustomerList() {
  return (
    <List
      filters={[
        <TextInput key="q" source="q" label="Search CIF, email, name" alwaysOn />,
        <SelectInput key="status" source="status" choices={choices(["registered", "blocked"])} alwaysOn />,
      ]}
      sort={{ field: "created_at", order: "DESC" }}
    >
      <Datagrid rowClick="show" bulkActionButtons={false}>
        <TextField source="cif" label="CIF" />
        <TextField source="full_name" label="Name" />
        <TextField source="email" />
        <TextField source="residence_country" label="Country" />
        <FunctionField source="status" render={(r: RaRecord) => <StatusChip value={r.status as string} />} />
        <TextField source="accounts" label="Accounts" />
        <TimeField source="created_at" label="Registered" />
      </Datagrid>
    </List>
  );
}

interface AccountRow {
  acc: string;
  product_id: string;
  size_cents: number;
  stage: string;
  status: string | null;
  balance_cents: number | null;
  equity_cents: number | null;
  created_at: number;
}
interface OrderRow {
  ord: string;
  product_id: string;
  size_cents: number;
  price_cents: number;
  status: string;
  created_at: number;
}

function CustomerDetail() {
  const r = useRecordContext();
  if (!r) return null;
  const accounts = (r.account_list ?? []) as AccountRow[];
  const orders = (r.orders ?? []) as OrderRow[];
  return (
    <Stack spacing={2} sx={{ mt: 1 }}>
      <Card variant="outlined">
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Accounts (${accounts.length})`} />
        <CardContent sx={{ pt: 0, overflowX: "auto" }}>
          <Table size="small" aria-label="accounts">
            <TableHead>
              <TableRow>
                {["Account", "Product", "Size", "Stage", "Status", "Balance", "Equity", "Opened"].map((h) => (
                  <TableCell key={h}>{h}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {accounts.map((a) => (
                <TableRow key={a.acc}>
                  <TableCell>{a.acc}</TableCell>
                  <TableCell>{a.product_id}</TableCell>
                  <TableCell>{money(a.size_cents)}</TableCell>
                  <TableCell>{STAGE_NAMES[a.stage] ?? a.stage}</TableCell>
                  <TableCell>
                    <StatusChip value={a.status} />
                  </TableCell>
                  <TableCell>{money(a.balance_cents)}</TableCell>
                  <TableCell>{money(a.equity_cents)}</TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{when(a.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card variant="outlined">
        <CardHeader slotProps={{ title: { component: "h2" } }} title={`Orders (${orders.length})`} />
        <CardContent sx={{ pt: 0, overflowX: "auto" }}>
          <Table size="small" aria-label="orders">
            <TableHead>
              <TableRow>
                {["Order", "Product", "Size", "Price", "Status", "Placed"].map((h) => (
                  <TableCell key={h}>{h}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {orders.map((o) => (
                <TableRow key={o.ord}>
                  <TableCell>{o.ord}</TableCell>
                  <TableCell>{o.product_id}</TableCell>
                  <TableCell>{money(o.size_cents)}</TableCell>
                  <TableCell>{money(o.price_cents)}</TableCell>
                  <TableCell>{o.status}</TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{when(o.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </Stack>
  );
}

export function CustomerShow() {
  return (
    <Show>
      <SimpleShowLayout>
        <TextField source="cif" label="CIF" />
        <TextField source="full_name" label="Name" />
        <TextField source="email" />
        <TextField source="date_of_birth" label="Date of birth" />
        <TextField source="residence_country" label="Country of residence" />
        <FunctionField source="status" render={(r: RaRecord) => <StatusChip value={r.status as string} />} />
        <TimeField source="created_at" label="Registered" />
        <CustomerDetail />
      </SimpleShowLayout>
    </Show>
  );
}

/** Every trading account with its owner and live state: evaluation and funded. */
export function AccountList() {
  return (
    <List
      filters={[
        <SelectInput key="stage" source="stage" choices={[{ id: "s1", name: "Stage 1" }, { id: "s2", name: "Stage 2" }, { id: "funded", name: "Funded" }]} alwaysOn />,
        <SelectInput key="status" source="status" choices={choices(["active", "passed", "breached", "voided"])} alwaysOn />,
      ]}
      sort={{ field: "created_at", order: "DESC" }}
    >
      <Datagrid bulkActionButtons={false}>
        <TextField source="acc" label="Account" />
        <FunctionField
          source="full_name"
          label="Owner"
          render={(r: RaRecord) => <Link to={`/customers/${String(r.cif)}/show`}>{String(r.full_name ?? r.cif)}</Link>}
        />
        <TextField source="product_id" label="Product" />
        <MoneyField source="size_cents" label="Size" />
        <FunctionField source="stage" render={(r: RaRecord) => STAGE_NAMES[String(r.stage)] ?? String(r.stage)} />
        <FunctionField source="status" render={(r: RaRecord) => <StatusChip value={r.status as string | null} />} />
        <MoneyField source="balance_cents" label="Balance" />
        <MoneyField source="equity_cents" label="Equity" />
        <TimeField source="created_at" label="Opened" />
      </Datagrid>
    </List>
  );
}
