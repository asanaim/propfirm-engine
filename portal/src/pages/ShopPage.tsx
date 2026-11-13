import { useEffect, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { PlacedOrder, Product, Stage } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { lossKindLabel } from "../lib/lossLimit";
import { formatCents, formatPercent, formatSize } from "../lib/money";
import { navigate } from "../router";

export function ordinal(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  const suffix = teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${suffix}`;
}

const STAGE_NAMES: Record<Stage["stage"], string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };

function StageRules({ stage }: { stage: Stage }) {
  return (
    <div className="stage">
      <h4>{STAGE_NAMES[stage.stage]}</h4>
      <dl className="facts">
        {stage.profit_target_percent !== null && (
          <>
            <dt>Profit target</dt>
            <dd>{formatPercent(stage.profit_target_percent)}</dd>
          </>
        )}
        <dt>Daily loss limit</dt>
        <dd>{formatPercent(stage.daily_loss_percent)}</dd>
        <dt>Maximum loss</dt>
        <dd>
          {formatPercent(stage.max_loss_percent)} ({lossKindLabel(stage.max_loss_kind)})
        </dd>
        <dt>Minimum trading days</dt>
        <dd>{stage.min_trading_days}</dd>
        <dt>Consistency</dt>
        <dd>
          {stage.consistency_percent === null ? "none" : `best day at most ${formatPercent(stage.consistency_percent)}`}
        </dd>
      </dl>
    </div>
  );
}

function ProductCard({ product, onBuy, busy }: { product: Product; onBuy: (size: number) => void; busy: boolean }) {
  const first = product.sizes[0]?.size_cents ?? 0;
  const [size, setSize] = useState(first);
  const price = product.sizes.find((s) => s.size_cents === size)?.price_cents;
  const id = `size-${product.id}`;
  return (
    <Card
      title={product.name}
      actions={
        <Button
          busy={busy}
          onClick={() => {
            onBuy(size);
          }}
        >
          Buy {formatSize(size)} for {price === undefined ? "—" : formatCents(price)}
        </Button>
      }
    >
      <p className="split">{product.profit_split_percent} % profit split</p>
      {product.fee_refund_with_payout !== null && (
        <p className="muted">Fee refunded with your {ordinal(product.fee_refund_with_payout)} payout.</p>
      )}
      <fieldset className="segmented">
        <legend>Account size</legend>
        {product.sizes.map((s) => (
          <label key={s.size_cents} className="segment">
            <input
              type="radio"
              name={id}
              value={s.size_cents}
              checked={s.size_cents === size}
              onChange={() => {
                setSize(s.size_cents);
              }}
            />
            <span>{formatSize(s.size_cents)}</span>
          </label>
        ))}
      </fieldset>
      <div className="stages">
        {product.stages.map((stage) => (
          <StageRules key={stage.rsv_id} stage={stage} />
        ))}
      </div>
    </Card>
  );
}

/** A way to pay, as the firm attached it (rule AC13). */
export interface PayMethod {
  method_id: string;
  name: string;
  min_cents: number;
  max_cents: number; // 0: no maximum
  fixed_charge_cents: number;
  percent_charge: string;
  instructions: string;
}

export function ShopPage() {
  const api = useApi();
  const auth = useAuth();
  const me = useMe();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [order, setOrder] = useState<PlacedOrder | null>(null);
  const [paid, setPaid] = useState(false);
  const [methods, setMethods] = useState<PayMethod[]>([]);
  const [method, setMethod] = useState<string | null>(null);

  useEffect(() => {
    api.get<Product[]>("/catalogue").then(setProducts, setError);
    api.get<PayMethod[]>("/payment-methods").then(
      (found) => {
        setMethods(found);
        setMethod(found[0]?.method_id ?? null);
      },
      () => {
        setMethods([]); // none attached: the checkout's own way to pay
      },
    );
  }, [api]);

  const buy = async (product: Product, size: number) => {
    if (auth.status !== "signed_in") {
      await auth.signIn("/shop");
      return;
    }
    if (me.status === "unregistered") {
      navigate("/");
      return;
    }
    setBusy(product.id);
    setError(null);
    setPaid(false);
    try {
      const body = { product_id: product.id, size_cents: size, ...(method === null ? {} : { method }) };
      setOrder(await api.post<PlacedOrder>("/orders", body));
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(null);
    }
  };

  const pay = async (placed: PlacedOrder) => {
    setError(null);
    try {
      await api.post(placed.pay_url); // sandbox: completes the payment
      setPaid(true);
      me.reload();
    } catch (reason) {
      setError(reason);
    }
  };

  return (
    <div className="page">
      <h1>Challenges</h1>
      <p className="lead">Pass the evaluation, then trade a funded account and keep most of the profit.</p>
      {error !== null && <Problem error={error} />}
      {methods.length > 0 && (
        <fieldset className="segmented pay-methods" aria-label="Pay with">
          <legend className="visually-hidden">Pay with</legend>
          {methods.map((m) => (
            <label key={m.method_id} className="segment">
              <input
                type="radio"
                name="pay-method"
                checked={method === m.method_id}
                onChange={() => {
                  setMethod(m.method_id);
                }}
              />
              <span>{m.name}</span>
            </label>
          ))}
        </fieldset>
      )}
      {order !== null && (
        <Card title={`Order ${order.ord}`}>
          <p>
            {formatSize(order.size_cents)} {order.product_id}: {formatCents(order.price_cents)}
          </p>
          {paid ? (
            <Notice tone="ok">Payment received. Your account opens in a moment under Accounts.</Notice>
          ) : order.pay_url.startsWith("/dev/") ? (
            <Button onClick={() => void pay(order)}>Complete payment (sandbox)</Button>
          ) : (
            <a className="button button-primary" href={order.pay_url} rel="noopener noreferrer">
              Pay with USDT (TRC-20)
            </a>
          )}
        </Card>
      )}
      {products === null && error === null ? (
        <Loading />
      ) : (
        <div className="grid products">
          {(products ?? []).map((p) => (
            <ProductCard key={p.id} product={p} busy={busy === p.id} onBuy={(size) => void buy(p, size)} />
          ))}
        </div>
      )}
    </div>
  );
}
