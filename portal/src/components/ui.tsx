import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Button({
  variant = "primary",
  busy = false,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet"; busy?: boolean }) {
  return (
    <button className={`button button-${variant}`} aria-busy={busy || undefined} disabled={busy || rest.disabled} {...rest}>
      {children}
    </button>
  );
}

export function Card({ title, children, actions }: { title?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {title !== undefined && <h2 className="card-title">{title}</h2>}
      {children}
      {actions !== undefined && <div className="card-actions">{actions}</div>}
    </section>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "ok"; children: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p className="loading" role="status">
      {label}…
    </p>
  );
}
