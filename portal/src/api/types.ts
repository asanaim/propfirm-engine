/** Shapes the API returns (see src/propfirm/api/app.py). Money is integer
cents, times integer UTC milliseconds, percentages exact text. */

export interface Stage {
  stage: "s1" | "s2" | "funded";
  rsv_id: string;
  profit_target_percent: string | null;
  max_loss_percent: string | null;
  max_loss_kind: "static" | "trailing";
  daily_loss_percent: string | null;
  min_trading_days: number;
  consistency_percent: string | null;
  consistency_at_pass: boolean;
  target_basis: "closed_balance" | "equity";
  flat_to_pass: boolean; // no open position or pending order to pass
  tie_rule: "breach_wins" | "pass_wins";
  news_restricted: boolean; // restricted news windows bind this stage (funded only, as FTMO)
  floor_restarts_after_payout?: boolean; // trailing loss: a payout lowers the floor again (older Instant only)
}

export interface Product {
  id: string;
  name: string;
  profit_split_percent: string;
  code_discount_max_percent: string | null;
  repeat_discount_percent: string[];
  fee_refund_with_payout: number | null;
  sizes: { size_cents: number; price_cents: number }[];
  stages: Stage[];
}

export interface Order {
  ord: string;
  product_id: string;
  size_cents: number;
  list_price_cents: number;
  discount_kind: string | null;
  discount_percent: string | null;
  discount_code: string | null;
  price_cents: number;
  rsv_id: string;
  status: string;
  acc: string | null;
  failure: string | null;
  created_at: number;
  paid_at: number | null;
}

export interface PlacedOrder extends Order {
  pay_url: string;
}

export interface AccountRecord {
  acc: string;
  ord: string;
  product_id: string;
  rsv_id: string;
  size_cents: number;
  stage: string;
  progression: string | null;
  onboarding: string | null;
  created_at: number;
}

export interface Customer {
  cif: string;
  email: string;
  full_name: string;
  date_of_birth: string;
  residence_country: string;
  status: string;
  created_at: number;
}

export interface Me extends Customer {
  accounts: AccountRecord[];
  orders: Order[];
  progressions: unknown[];
  onboardings: Onboarding[];
}

/** Funded onboarding (ONB): identity check, review, contract, signature. */
export type OnboardingStatus =
  | "verification"
  | "verification_declined"
  | "checks"
  | "review"
  | "rejected"
  | "contract"
  | "signature"
  | "signed"
  | "provisioned"
  | "failed";

export interface Onboarding {
  onb: string;
  cif: string;
  source: string;
  prg: string | null;
  ord: string | null;
  product_id: string;
  size_cents: number;
  rsv_id: string;
  status: OnboardingStatus;
  kyc: string | null;
  kyc_attempts: number;
  acc: string | null;
  contract: string | null;
  failure: string | null;
  created_at: number;
  verification_url?: string | null;
}

export interface Contract {
  contract: string;
  template: string;
  sha256: string;
  draft: boolean;
  body: string;
  created_at: number;
}

export interface Wallet {
  wal: string;
  network: string;
  address: string;
  added_at: number;
}

export interface EligibilityCheck {
  name: string;
  status: string; // "pass", "fail", ...
  detail: string;
}

export interface Eligibility {
  acc: string;
  as_of: number;
  eligible: boolean;
  basis: string;
  payable_cents: number;
  split_percent: string;
  trader_share_cents: number;
  firm_share_cents: number;
  fee_refund_cents: number;
  trader_amount_cents: number;
  debit_cents: number;
  trading_days: number;
  opens_at: number | null;
  checks: EligibilityCheck[];
}

export type PayoutStatus =
  | "requested"
  | "approved"
  | "debited"
  | "sent"
  | "paid"
  | "denied"
  | "cancelled"
  | "failed";

export interface Payout {
  po: string;
  acc: string;
  cif: string;
  wal: string;
  requested_at: number;
  trader_amount_cents: number;
  debit_cents: number;
  status: PayoutStatus;
  approvals: number;
  debited_at: number | null;
  paid_at: number | null;
  provider_ref: string | null;
  failure: string | null;
}

export interface Registration {
  full_name: string;
  date_of_birth: string; // YYYY-MM-DD
  residence_country: string; // ISO 3166-1 alpha-2
}

export interface LivePosition {
  position_id: string;
  symbol: string;
  side: "buy" | "sell";
  volume: number; // hundredths of a lot
  open_price: number; // points
  opened_at: number;
  stop_loss: number | null;
  take_profit: number | null;
  current_price: number; // what it would close at now (bid for a buy, ask for a sell)
  pnl_cents: number; // floating, at current_price (display)
}

export interface LivePendingOrder {
  request: {
    order_id: string;
    symbol: string;
    side: "buy" | "sell";
    order_type: "market" | "limit" | "stop";
    volume: number;
    level: number | null;
    stop_loss: number | null;
    take_profit: number | null;
  };
  placed_at: number;
}

/** A working trailing order (M09): a trailing buy or sell (`entry`), or a
position's trailing stop. Levels, distance and step are integer points. */
export interface LiveTrailingOrder {
  order_id: string;
  symbol: string;
  side: "buy" | "sell"; // the position's side: opened by an entry, protected by a stop
  kind: "entry" | "stop";
  volume: number | null; // entries only
  position_id: string | null; // stops only
  distance: number;
  step: number;
  level: number; // the current trigger level
  moved_at: number; // engine time of the price that set the level (ms UTC)
  moved_seq: number; // that price update's id
  placed_at: number;
}

/** GET /accounts/{acc} and the stream's `account` event (engine live state). */
export interface LiveAccount {
  account_id: string;
  rsv_id: string;
  status: "active" | "breached" | "passed" | "voided";
  locked: boolean;
  balance_cents: number;
  equity_cents: number;
  used_margin_cents: number;
  free_margin_cents: number;
  initial_balance_cents: number;
  daily_floor_cents: number;
  max_floor_cents: number;
  daily_loss_limit_cents: number;
  max_loss_limit_cents: number;
  next_reset_ts: number;
  next_daily_floor_cents: number;
  consistency_ok: boolean;
  target_balance_cents: number | null;
  rules: Stage;
  trading_days: number;
  positions: LivePosition[];
  pending_orders: LivePendingOrder[];
  trailing_orders: LiveTrailingOrder[];
  engine_ts: number;
}

export interface Quote {
  symbol: string;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  source_ts: number | null;
  digits: number;
  frozen: boolean;
  blocked: boolean;
}

/** The price source, to show beside every chart and price table. */
export interface Attribution {
  text: string; // e.g. "Data provided by Twelve Data"
  link: string | null; // the part of the text that links ("Twelve Data")
  url: string | null;
}

export interface Market {
  source: "synthetic" | "twelve_data";
  attribution: Attribution;
  symbols: { symbol: string; digits: number }[];
  intervals: string[];
}

export interface Bar {
  time: number; // ms UTC, the bar's start
  open: number; // points
  high: number;
  low: number;
  close: number;
  updates: number;
}
