import { useState } from "react";
import { useApi } from "../api/ApiProvider";
import type { LivePendingOrder, LivePosition } from "../api/types";
import { formatCents, formatLots, formatPrice } from "../lib/money";
import { parsePrice } from "../lib/price";
import { describeOutcome, type Outcome } from "./OrderTicket";

function useAction(digits: number) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null);
  const run = async (act: () => Promise<Outcome>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(describeOutcome(await act(), digits));
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Failed." });
    } finally {
      setBusy(false);
    }
  };
  return { busy, message, run };
}

function Message({ message }: { message: { tone: string; text: string } | null }) {
  if (message === null) return null;
  return (
    <span className={message.tone === "error" ? "loss" : "muted"} role={message.tone === "error" ? "alert" : "status"}>
      {message.text}
    </span>
  );
}

/** An open position: its floating P&L, close, and stop loss / take profit. */
export function PositionRow({ acc, position: p, digits, canTrade }: { acc: string; position: LivePosition; digits: number; canTrade: boolean }) {
  const api = useApi();
  const { busy, message, run } = useAction(digits);
  const [editing, setEditing] = useState(false);
  const [sl, setSl] = useState(p.stop_loss === null ? "" : formatPrice(p.stop_loss, digits));
  const [tp, setTp] = useState(p.take_profit === null ? "" : formatPrice(p.take_profit, digits));
  const slPoints = sl.trim() === "" ? null : parsePrice(sl, digits);
  const tpPoints = tp.trim() === "" ? null : parsePrice(tp, digits);
  const valid = (sl.trim() === "" || slPoints !== null) && (tp.trim() === "" || tpPoints !== null);
  const base = `/accounts/${acc}/positions/${encodeURIComponent(p.position_id)}`;
  const label = `${p.side === "buy" ? "Buy" : "Sell"} ${formatLots(p.volume)} ${p.symbol}`;
  return (
    <tr>
      <th scope="row">{p.symbol}</th>
      <td>{p.side === "buy" ? "Buy" : "Sell"}</td>
      <td>{formatLots(p.volume)}</td>
      <td>{formatPrice(p.open_price, digits)}</td>
      <td>{formatPrice(p.current_price, digits)}</td>
      <td className={p.pnl_cents < 0 ? "loss" : undefined}>{formatCents(p.pnl_cents)}</td>
      <td>
        {editing ? (
          <span className="inline-form">
            <label>
              <span className="visually-hidden">Stop loss for {label}</span>
              <input inputMode="decimal" placeholder="Stop loss" value={sl} onChange={(e) => { setSl(e.target.value); }} />
            </label>
            <label>
              <span className="visually-hidden">Take profit for {label}</span>
              <input inputMode="decimal" placeholder="Take profit" value={tp} onChange={(e) => { setTp(e.target.value); }} />
            </label>
          </span>
        ) : (
          <>
            {p.stop_loss === null ? "—" : formatPrice(p.stop_loss, digits)} /{" "}
            {p.take_profit === null ? "—" : formatPrice(p.take_profit, digits)}
          </>
        )}
      </td>
      <td className="actions">
        {canTrade && editing && (
          <>
            <button
              type="button"
              className="button button-secondary small"
              disabled={busy || !valid}
              onClick={() =>
                void run(async () => {
                  const outcome = await api.put<Outcome>(`${base}/stops`, { stop_loss: slPoints, take_profit: tpPoints });
                  setEditing(false);
                  return outcome;
                })
              }
            >
              Save
            </button>
            <button type="button" className="button button-quiet small" onClick={() => { setEditing(false); }}>
              Cancel
            </button>
          </>
        )}
        {canTrade && !editing && (
          <>
            <button type="button" className="button button-secondary small" aria-label={`Change stops of ${label}`} onClick={() => { setEditing(true); }}>
              Stops
            </button>
            <button
              type="button"
              className="button button-secondary small"
              aria-label={`Close ${label}`}
              disabled={busy}
              onClick={() => void run(() => api.post<Outcome>(`${base}/close`, {}))}
            >
              Close
            </button>
          </>
        )}
        <Message message={message} />
      </td>
    </tr>
  );
}

/** A pending order and its cancel button. */
export function PendingRow({ acc, order, digits, canTrade }: { acc: string; order: LivePendingOrder; digits: number; canTrade: boolean }) {
  const api = useApi();
  const { busy, message, run } = useAction(digits);
  const o = order.request;
  const label = `${o.order_type} ${o.side} ${formatLots(o.volume)} ${o.symbol}`;
  return (
    <tr>
      <th scope="row">{o.symbol}</th>
      <td>{o.order_type === "limit" ? "Limit" : o.order_type === "stop" ? "Stop" : "Market"}</td>
      <td>{o.side === "buy" ? "Buy" : "Sell"}</td>
      <td>{formatLots(o.volume)}</td>
      <td>{o.level === null ? "—" : formatPrice(o.level, digits)}</td>
      <td className="actions">
        {canTrade && (
          <button
            type="button"
            className="button button-secondary small"
            aria-label={`Cancel ${label}`}
            disabled={busy}
            onClick={() => void run(() => api.delete<Outcome>(`/accounts/${acc}/orders/${encodeURIComponent(o.order_id)}`))}
          >
            Cancel
          </button>
        )}
        <Message message={message} />
      </td>
    </tr>
  );
}
