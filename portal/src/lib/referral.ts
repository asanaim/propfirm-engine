/** Referral codes from links (D-14.15): `/?ref=CODE` is kept for this
browser session, across the sign-in redirect, and sent once at
registration. Storage can be missing or refused; then the code is lost,
which only loses the attribution. */

const KEY = "pipridge.ref";
const CODE = /^[A-Za-z0-9]{8}$/;

export function captureRef(search: string): void {
  const ref = new URLSearchParams(search).get("ref");
  if (ref === null || !CODE.test(ref)) return;
  try {
    sessionStorage.setItem(KEY, ref.toUpperCase());
  } catch {
    // storage refused: no attribution
  }
}

export function storedRef(): string | null {
  try {
    const ref = sessionStorage.getItem(KEY);
    return ref !== null && CODE.test(ref) ? ref : null;
  } catch {
    return null;
  }
}

export function clearRef(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing kept
  }
}
