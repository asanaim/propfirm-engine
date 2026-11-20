import { Badge, Button, ClickAwayListener, IconButton, Paper, Popper, Tooltip } from "@mui/material";
import { Bell } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useBackOffice } from "./context";
import { when } from "./format";
import { STROKE } from "./icons";
import { recordPath } from "./places";

/** How often the bell asks for open notifications. */
export const BELL_POLL_MS = 60_000;

/** The most notifications the panel lists; the rest are on the full page. */
export const PANEL_LIMIT = 8;

/** An open notification as the API gives it. */
export interface OpenNotification {
  id: number;
  kind: string;
  ref: string | null;
  title: string;
  body: string;
  urgent: boolean;
  ts: number;
}

/** The count shown on the bell: all open notifications; urgent turns it red. */
export function bellState(open: readonly Pick<OpenNotification, "urgent">[]): {
  count: number;
  urgent: boolean;
} {
  return { count: open.length, urgent: open.some((n) => n.urgent) };
}

/** What the panel lists: urgent first, then newest first, at most `limit`. */
export function panelItems(open: readonly OpenNotification[], limit = PANEL_LIMIT): OpenNotification[] {
  return [...open]
    .sort((a, b) => Number(b.urgent) - Number(a.urgent) || b.ts - a.ts || b.id - a.id)
    .slice(0, limit);
}

/** Notifications in the top bar, beside the account button: a bell with
the number of open notifications, polled every minute and on each page
change. Clicking it opens a small floating panel under the bell (owner,
19 Nov): no backdrop and no blur, the page stays usable; a click outside,
Escape or a page change closes it. */
export function NotificationBell() {
  const { api } = useBackOffice();
  const { pathname } = useLocation();
  const [open, setOpen] = useState<OpenNotification[]>([]);
  // the page the panel was opened on: another page means it is closed
  const [openedOn, setOpenedOn] = useState<string | null>(null);
  const shown = openedOn === pathname;
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);

  const load = useCallback(() => {
    api.get<OpenNotification[]>("/staff/notifications").then(
      (list) => {
        setOpen(list);
        setFailed(false);
      },
      () => {
        setFailed(true); // the bell keeps its last count; the panel says so
      },
    );
  }, [api]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, BELL_POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [load, pathname]);

  const close = useCallback(() => {
    setOpenedOn(null);
    anchor?.focus();
  }, [anchor]);

  useEffect(() => {
    if (!shown) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [shown, close]);

  const acknowledge = (id: number) => {
    setBusy(id);
    api.post(`/staff/notifications/${id.toString()}/ack`, {}).then(
      () => {
        setOpen((list) => list.filter((n) => n.id !== id));
        setBusy(null);
      },
      () => {
        setBusy(null);
        setFailed(true);
      },
    );
  };

  const state = bellState(open);
  const label = state.count === 0 ? "Notifications, none open" : `Notifications, ${state.count.toString()} open`;
  const items = panelItems(open);
  return (
    <>
      <Tooltip title={shown ? "" : "Notifications"}>
        <IconButton
          ref={setAnchor}
          color="inherit"
          aria-label={label}
          aria-haspopup="dialog"
          aria-expanded={shown}
          aria-controls={shown ? "notification-panel" : undefined}
          className="brand-bar-bell"
          onClick={() => {
            if (!shown) load();
            setOpenedOn(shown ? null : pathname);
          }}
        >
          <Badge badgeContent={state.count} color={state.urgent ? "error" : "primary"} max={99}>
            <Bell size={20} strokeWidth={STROKE} aria-hidden="true" />
          </Badge>
        </IconButton>
      </Tooltip>
      <Popper
        open={shown}
        anchorEl={anchor}
        placement="bottom-end"
        className="notification-popper"
        modifiers={[{ name: "offset", options: { offset: [0, 8] } }]}
      >
        <ClickAwayListener
          onClickAway={(e) => {
            if (anchor?.contains(e.target as Node) !== true) setOpenedOn(null);
          }}
        >
          <Paper id="notification-panel" role="dialog" aria-label="Open notifications" className="notification-panel" elevation={0}>
            <header className="notification-panel-head">
              <h2>Notifications</h2>
              <span className="muted">{state.count === 0 ? "none open" : `${state.count.toString()} open`}</span>
            </header>
            {failed ? <p className="notification-panel-note">Could not refresh; showing the last list.</p> : null}
            {items.length === 0 ? (
              <p className="notification-panel-empty">Nothing waits for you.</p>
            ) : (
              <ul className="notification-panel-list">
                {items.map((n) => {
                  const path = recordPath(n.ref);
                  return (
                    <li key={n.id} className={n.urgent ? "urgent" : undefined}>
                      <div className="notification-panel-title">
                        {n.urgent ? <span className="notification-panel-dot" aria-label="urgent" /> : null}
                        {path === null ? <span>{n.title}</span> : <Link to={path}>{n.title}</Link>}
                      </div>
                      {n.body === "" ? null : <p className="notification-panel-body">{n.body}</p>}
                      <div className="notification-panel-meta">
                        <span>{when(n.ts)}</span>
                        <Button
                          size="small"
                          disabled={busy === n.id}
                          onClick={() => {
                            acknowledge(n.id);
                          }}
                        >
                          Acknowledge
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            <footer className="notification-panel-foot">
              <Link to="/notifications">
                {state.count > items.length ? `See all ${state.count.toString()}` : "See all, including done"}
              </Link>
            </footer>
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}
