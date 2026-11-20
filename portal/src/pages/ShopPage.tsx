import { useEffect, useState } from "react";
import { useApi, useMe } from "../api/ApiProvider";
import type { PlacedOrder, Product, RetakeOffer, Stage, TrialOffer } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, Notice } from "../components/ui";
import { lossKindLabel } from "../lib/lossLimit";
import { formatCents, formatPercent, formatSize } from "../lib/money";
import { navigate } from "../router";


/** The split ladder in a line, or null for a product without one (Instant). */
export function ladderText(p: { split_after_payouts?: number | null; split_percent_after_payouts?: string | null }): string | null {
  if (p.split_after_payouts === null || p.split_after_payouts === undefined || !p.split_percent_after_payouts) return null;
  return `${p.split_percent_after_payouts} % after ${p.split_after_payouts.toString()} paid payouts, more with scaling`;
}

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

/** What each add-on does (D-13.18). */
export const ADDON_TEXT: Record<string, string> = {
  split90: "90 % profit split from your first payout",
  swing: "Swing: hold over news and weekends (leverage up to 1:30)",
  swapfree: "No swap on the first 5 nights of each position",
};

/** `percent` (exact text, "15" or "12.5") of `cents`, rounded down, as the
server prices it. Whole numbers only, so the result is exact. */
export function percentOf(cents: number, percent: string): number {
  const [whole = "", part = ""] = percent.split(".");
  const scale = 10 ** part.length;
  return Math.floor((cents * Number(whole + part)) / (100 * scale));
}

function ProductCard({ product, onBuy, busy }: { product: Product; onBuy: (size: number, addons: string[]) => void; busy: boolean }) {
  const first = product.sizes[0]?.size_cents ?? 0;
  const [size, setSize] = useState(first);
  const [chosen, setChosen] = useState<string[]>([]);
  const list = product.sizes.find((s) => s.size_cents === size)?.price_cents;
  const offered = product.addons ?? [];
  const price = list === undefined ? undefined : list + offered.filter((a) => chosen.includes(a.id)).reduce((sum, a) => sum + percentOf(list, a.price_percent), 0);
  const id = `size-${product.id}`;
  return (
    <Card
      title={product.name}
      actions={
        <Button
          busy={busy}
          onClick={() => {
            onBuy(size, chosen);
          }}
        >
          Buy {formatSize(size)} for {price === undefined ? "—" : formatCents(price)}
        </Button>
      }
    >
      <div className="product-choose">
        <p className="split">{product.profit_split_percent} % profit split</p>
        {ladderText(product) === null ? null : <p className="split-ladder muted">{ladderText(product)}</p>}
        {product.retake_discount_percent ? (
          <p className="muted">After a breach: one retake of the same size at {product.retake_discount_percent} % off.</p>
        ) : null}
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
      </div>
      <div className="product-detail">
        <div className="stages">
          {product.stages.map((stage) => (
            <StageRules key={stage.rsv_id} stage={stage} />
          ))}
        </div>
        {offered.length > 0 ? (
          <fieldset className="addons product-addons">
            <legend>Add-ons</legend>
            {offered.map((a) => (
              <label key={a.id} className="addon">
                <input
                  type="checkbox"
                  checked={chosen.includes(a.id)}
                  onChange={(e) => {
                    setChosen(e.target.checked ? [...chosen, a.id] : chosen.filter((c) => c !== a.id));
                  }}
                />
                <span>
                  {ADDON_TEXT[a.id] ?? a.id} <span className="muted">+{a.price_percent} %</span>
                </span>
              </label>
            ))}
          </fieldset>
        ) : null}
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
  const [retakes, setRetakes] = useState<RetakeOffer[]>([]);
  const [trial, setTrial] = useState<TrialOffer | null>(null);
  const [trialAcc, setTrialAcc] = useState<string | null>(null);
  const ready = me.status === "ready";

  useEffect(() => {
    api.get<Product[]>("/catalogue").then(setProducts, setError);
    api.get<TrialOffer>("/trial").then(setTrial, () => {
      setTrial(null); // no trial shown when the offer cannot be read
    });
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

  useEffect(() => {
    if (!ready) return;
    api.get<RetakeOffer[]>("/me/retakes").then(setRetakes, () => {
      setRetakes([]); // no offer shown when the list cannot be read
    });
  }, [api, ready, order]);

  const buy = async (product: Product, size: number, addons: string[] = []) => {
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
      const body = { product_id: product.id, size_cents: size, ...(addons.length > 0 ? { addons } : {}), ...(method === null ? {} : { method }) };
      setOrder(await api.post<PlacedOrder>("/orders", body));
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(null);
    }
  };

  const startTrial = async () => {
    if (auth.status !== "signed_in") {
      await auth.signIn("/shop");
      return;
    }
    if (me.status === "unregistered") {
      navigate("/");
      return;
    }
    setBusy("trial");
    setError(null);
    try {
      const started = await api.post<PlacedOrder>("/me/trial");
      setTrialAcc(started.acc);
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
    <div className="page shop">
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
      {trial?.on === true ? (
        <Card
          title="Free trial"
          actions={
            trialAcc === null ? (
              <Button busy={busy === "trial"} variant="secondary" onClick={() => void startTrial()}>
                Start free trial
              </Button>
            ) : (
              <Button
                onClick={() => {
                  navigate(`/accounts/${trialAcc}`);
                }}
              >
                Open your trial
              </Button>
            )
          }
        >
          <p>
            {formatSize(trial.size_cents)} {(products ?? []).find((p) => p.id === trial.product_id)?.name ?? trial.product_id}, stage 1 rules, {trial.days} days. Free, one per
            trader. Practice only: no payout and no next stage.
          </p>
        </Card>
      ) : null}
      {retakes.map((r) => {
        const product = (products ?? []).find((p) => p.id === r.product_id);
        if (product === undefined) return null;
        return (
          <Card
            key={r.of_ord}
            title={`Retake ${formatSize(r.size_cents)} ${product.name}`}
            actions={
              <Button busy={busy === product.id} onClick={() => void buy(product, r.size_cents)}>
                Retake for {formatCents(r.price_cents)}
              </Button>
            }
          >
            <p>
              <s className="muted">{formatCents(r.list_price_cents)}</s> {r.discount_percent} % off after your breach.
            </p>
          </Card>
        );
      })}
      {products === null && error === null ? (
        <Loading />
      ) : (
        <div className="grid products">
          {(products ?? []).map((p) => (
            <ProductCard key={p.id} product={p} busy={busy === p.id} onBuy={(size, addons) => void buy(p, size, addons)} />
          ))}
        </div>
      )}
    </div>
  );
}
