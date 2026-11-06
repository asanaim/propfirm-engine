import { FunctionField, type RaRecord } from "react-admin";
import { money, when } from "./format";

/** A money column: integer cents shown as dollars. */
export function MoneyField({ source, label }: { source: string; label?: string }) {
  return (
    <FunctionField
      source={source}
      label={label ?? source}
      render={(r: RaRecord) => money(r[source] as number | null | undefined)}
    />
  );
}

/** A time column: epoch milliseconds shown in UTC. */
export function TimeField({ source, label }: { source: string; label?: string }) {
  return (
    <FunctionField
      source={source}
      label={label ?? source}
      render={(r: RaRecord) => when(r[source] as number | null | undefined)}
    />
  );
}

/** Any JSON value, read-only. */
export function JsonField({ source, label }: { source: string; label?: string }) {
  return (
    <FunctionField
      source={source}
      label={label ?? source}
      render={(r: RaRecord) => <pre className="json">{JSON.stringify(r[source], null, 2)}</pre>}
    />
  );
}
