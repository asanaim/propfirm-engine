/** Money is integer cents (CLAUDE.md); shown in US dollars without floats. */
export function formatCents(cents: number): string {
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError(`not a whole number of cents: ${cents}`);
  }
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.trunc(abs / 100).toString();
  const rest = (abs % 100).toString().padStart(2, "0");
  const grouped = dollars.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}$${grouped}.${rest}`;
}

/** An allocation such as 10,000.00 shown as "$10K" on product cards. */
export function formatSize(cents: number): string {
  if (!Number.isSafeInteger(cents) || cents < 0) {
    throw new RangeError(`not a size in cents: ${cents}`);
  }
  const dollars = Math.trunc(cents / 100);
  if (cents % 100 === 0 && dollars % 1000 === 0 && dollars >= 1000) {
    return `$${(dollars / 1000).toString()}K`;
  }
  return formatCents(cents);
}

/** Exact percent text from the API ("8", "4.5"); null: no such rule. */
export function formatPercent(text: string | null): string {
  return text === null ? "none" : `${text} %`;
}

/** UTC milliseconds as a date in the viewer's locale (display only). */
export function formatDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** A price in integer points shown with the symbol's digits (no floats). */
export function formatPrice(points: number, digits: number): string {
  if (!Number.isSafeInteger(points) || !Number.isInteger(digits) || digits < 0) {
    throw new RangeError(`not a price: ${points} (${digits} digits)`);
  }
  const sign = points < 0 ? "-" : "";
  const text = Math.abs(points).toString().padStart(digits + 1, "0");
  if (digits === 0) return `${sign}${text}`;
  return `${sign}${text.slice(0, -digits)}.${text.slice(-digits)}`;
}

/** Lots from hundredths of a lot: 150 -> "1.50". */
export function formatLots(volume: number): string {
  return formatPrice(volume, 2);
}

/** A countdown such as "3 h 07 min" (display only). */
export function formatDuration(ms: number): string {
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  const hours = Math.floor(minutes / 60);
  const rest = (minutes % 60).toString().padStart(2, "0");
  return hours > 0 ? `${hours} h ${rest} min` : `${minutes % 60} min`;
}
