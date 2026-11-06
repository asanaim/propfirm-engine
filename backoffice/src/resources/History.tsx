import { Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import { useRecordContext } from "react-admin";
import { when } from "../format";

interface Event {
  kind: string;
  ts: number;
  detail?: unknown;
}

/** A record's history (append-only events), oldest first. */
export function History({ source = "history" }: { source?: string }) {
  const record = useRecordContext();
  const events = (record?.[source] as Event[] | undefined) ?? [];
  if (events.length === 0) return <p>No history.</p>;
  return (
    <Table size="small" aria-label="history">
      <TableHead>
        <TableRow>
          <TableCell>When</TableCell>
          <TableCell>What</TableCell>
          <TableCell>Detail</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {events.map((e, i) => (
          <TableRow key={`${e.ts}-${i}`}>
            <TableCell>{when(e.ts)}</TableCell>
            <TableCell>{e.kind}</TableCell>
            <TableCell>
              <code>{e.detail === undefined ? "" : JSON.stringify(e.detail)}</code>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
