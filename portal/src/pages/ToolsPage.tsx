import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import { Loading, Notice } from "../components/ui";
import { ToolsNav } from "./ToolsExtra";
import { margin, parse, pipValue, positionSize, profit, type ToolInstrument, usd, usdPerQuote } from "../lib/calculators";

type Tool = "size" | "pip" | "margin" | "profit";

const TOOLS: { key: Tool; label: string }[] = [
  { key: "size", label: "Position size" },
  { key: "pip", label: "Pip value" },
  { key: "margin", label: "Margin" },
  { key: "profit", label: "Profit and loss" },
];

function Field({ label, value, onChange, hint }: { label: string; value: string; onChange: (v: string) => void; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input inputMode="decimal" value={value} onChange={(e) => { onChange(e.target.value); }} />
      {hint === undefined ? null : <small className="muted">{hint}</small>}
    </label>
  );
}

function Answer({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="tool-answer" aria-live="polite">
      <span className="muted">{label}</span>
      <strong>{value ?? "Fill in the fields"}</strong>
    </div>
  );
}

/** Calculators for planning trades (lot size, pip value, margin, profit),
public like FTMO's and FundedNext's. Prices are typed by the trader: the
firm's licensed prices are for signed-in users only. */
export function ToolsPage() {
  const api = useApi();
  const [list, setList] = useState<ToolInstrument[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tool, setTool] = useState<Tool>("size");
  const [symbol, setSymbol] = useState("EUR/USD");
  const [v, setV] = useState({ balance: "100000", risk: "1", stop: "20", lots: "1", price: "", open: "", close: "", rate: "" });
  const [side, setSide] = useState<"buy" | "sell">("buy");
  useEffect(() => {
    api.get<ToolInstrument[]>("/tools/instruments").then(setList, () => {
      setFailed(true);
    });
  }, [api]);
  if (failed) return <Notice tone="error">The calculators could not load the symbols. Try again later.</Notice>;
  if (list === null) return <Loading />;
  const spec = list.find((i) => i.symbol === symbol) ?? list[0];
  if (spec === undefined) return <Notice tone="error">No symbols are set up.</Notice>;
  const set = (k: keyof typeof v) => (x: string) => {
    setV({ ...v, [k]: x });
  };
  const priceFor = tool === "profit" ? parse(v.close) : parse(v.price);
  const needsRate = spec.quote_currency !== "USD" && !spec.symbol.startsWith("USD/");
  const needsPrice = tool === "margin" || (spec.symbol.startsWith("USD/") && spec.quote_currency !== "USD");
  const usdRate = usdPerQuote(spec, priceFor, parse(v.rate));
  const lots = parse(v.lots);
  let answer: { label: string; value: string | null };
  if (tool === "size") {
    const balance = parse(v.balance);
    const risk = parse(v.risk);
    const stop = parse(v.stop);
    const size = balance !== null && risk !== null && stop !== null && usdRate !== null ? positionSize(balance, risk, stop, spec, usdRate) : null;
    answer = { label: "Lot size", value: size === null ? null : `${size.toFixed(2)} lots (risk ${usd(((balance ?? 0) * (risk ?? 0)) / 100)})` };
  } else if (tool === "pip") {
    answer = { label: `Value of one ${spec.pip_name}`, value: lots !== null && usdRate !== null ? usd(pipValue(spec, lots, usdRate)) : null };
  } else if (tool === "margin") {
    const price = parse(v.price);
    answer = { label: "Margin needed", value: lots !== null && price !== null && usdRate !== null ? usd(margin(spec, lots, price, usdRate)) : null };
  } else {
    const open = parse(v.open);
    const close = parse(v.close);
    answer = { label: side === "buy" ? "Profit on the buy" : "Profit on the sell", value: lots !== null && open !== null && close !== null && usdRate !== null ? usd(profit(spec, side, lots, open, close, usdRate)) : null };
  }
  return (
    <div className="page tools">
      <h1>Trading calculators</h1>
      <p className="lead">Plan a trade before you place it. Account currency: US dollars. These are estimates; your account page shows the platform&apos;s own figures.</p>
      <ToolsNav />
      <div className="tool-tabs tool-tabs-small" role="tablist" aria-label="Calculators">
        {TOOLS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tool === t.key} className={tool === t.key ? "active" : undefined} onClick={() => { setTool(t.key); }}>
            {t.label}
          </button>
        ))}
      </div>
      <section className="card tool" aria-label={TOOLS.find((t) => t.key === tool)?.label}>
        <div className="tool-grid">
          <label className="field">
            <span>Symbol</span>
            <select value={spec.symbol} onChange={(e) => { setSymbol(e.target.value); }}>
              {list.map((i) => (
                <option key={i.symbol}>{i.symbol}</option>
              ))}
            </select>
            <small className="muted">
              1 lot = {spec.contract_size.toLocaleString("en-US")} units; 1 {spec.pip_name} = {spec.pip}; leverage 1:{spec.leverage.split("/")[0]}
            </small>
          </label>
          {tool === "size" ? (
            <>
              <Field label="Account balance (USD)" value={v.balance} onChange={set("balance")} />
              <Field label="Risk per trade (%)" value={v.risk} onChange={set("risk")} />
              <Field label={`Stop loss (${spec.pip_name}s)`} value={v.stop} onChange={set("stop")} />
            </>
          ) : (
            <Field label="Lots" value={v.lots} onChange={set("lots")} />
          )}
          {tool === "profit" ? (
            <>
              <label className="field">
                <span>Direction</span>
                <select value={side} onChange={(e) => { setSide(e.target.value === "sell" ? "sell" : "buy"); }}>
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
              </label>
              <Field label="Open price" value={v.open} onChange={set("open")} />
              <Field label="Close price" value={v.close} onChange={set("close")} />
            </>
          ) : needsPrice ? (
            <Field label={`Current price of ${spec.symbol}`} value={v.price} onChange={set("price")} />
          ) : null}
          {needsRate ? <Field label={`1 ${spec.quote_currency} in USD`} value={v.rate} onChange={set("rate")} hint={`The ${spec.quote_currency}/USD rate, to turn ${spec.quote_currency} into dollars`} /> : null}
        </div>
        <Answer {...answer} />
      </section>
    </div>
  );
}
