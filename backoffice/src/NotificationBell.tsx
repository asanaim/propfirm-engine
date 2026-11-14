import { Badge, IconButton, Tooltip } from "@mui/material";
import { Bell } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useBackOffice } from "./context";
import { STROKE } from "./icons";

/** How often the bell asks for open notifications. */
export const BELL_POLL_MS = 60_000;

interface Open {
  urgent: boolean;
}

/** The count shown on the bell: all open notifications; urgent turns it red. */
export function bellState(open: readonly Open[]): {
  count: number;
  urgent: boolean;
} {
  return { count: open.length, urgent: open.some((n) => n.urgent) };
}

/** Notifications in the top bar, beside the account button: a bell with
the number of open notifications, polled every minute and on each page
change; it opens the notifications list. */
export function NotificationBell() {
  const { api } = useBackOffice();
  const { pathname } = useLocation();
  const [state, setState] = useState({ count: 0, urgent: false });
  useEffect(() => {
    let live = true;
    const load = () => {
      api.get<Open[]>("/staff/notifications").then(
        (open) => {
          if (live) setState(bellState(open));
        },
        () => undefined, // the bell stays as it was; the list page shows errors
      );
    };
    load();
    const timer = window.setInterval(load, BELL_POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [api, pathname]);
  const label =
    state.count === 0
      ? "Notifications, none open"
      : `Notifications, ${state.count.toString()} open`;
  return (
    <Tooltip title="Notifications">
      <IconButton
        component={Link}
        to="/notifications"
        color="inherit"
        aria-label={label}
        className="brand-bar-bell"
      >
        <Badge
          badgeContent={state.count}
          color={state.urgent ? "error" : "primary"}
          max={99}
        >
          <Bell size={20} strokeWidth={STROKE} aria-hidden="true" />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}
