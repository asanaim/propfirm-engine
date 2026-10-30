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
  onboardings: unknown[];
}

export interface Registration {
  full_name: string;
  date_of_birth: string; // YYYY-MM-DD
  residence_country: string; // ISO 3166-1 alpha-2
}
