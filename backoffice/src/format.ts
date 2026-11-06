/** Display only (the edge): integer cents and epoch milliseconds come from
the API; nothing here is fed back into a decision. */

/** 123456 → "1,234.56 USD". Integer arithmetic; no rounding happens. */
export function money(cents: number | null | undefined, currency = "USD"): string {
  if (cents === null || cents === undefined) return "-";
  if (!Number.isSafeInteger(cents)) throw new Error(`not integer cents: ${String(cents)}`);
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const units = Math.trunc(abs / 100).toLocaleString("en-US");
  const rest = String(abs % 100).padStart(2, "0");
  return `${sign}${units}.${rest} ${currency}`;
}

/** Epoch ms → "2026-11-06 09:30 UTC" (UTC: the staff sit in several zones). */
export function when(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "-";
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}
