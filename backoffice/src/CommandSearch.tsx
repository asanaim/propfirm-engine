import { Box, Button, Dialog, InputAdornment, List, ListItemButton, ListItemIcon, ListItemText, ListSubheader, TextField, Typography } from "@mui/material";
import { Fragment, type ReactNode, useContext, useEffect, useState } from "react";
import { usePermissions } from "react-admin";
import { useNavigate } from "react-router-dom";
import { BackOfficeContext } from "./context";
import { ICONS, SIZE, STROKE } from "./icons";
import { type Hit, rememberHit, search } from "./places";
import { screensFor } from "./screens";

/** Whether a key press is Ctrl+K (or Cmd+K on a Mac). */
export function isOpenKey(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k";
}

/** How long typing pauses before traders are looked up. */
export const TRADER_DELAY_MS = 200;
const TRADER_LIMIT = 5;

interface TraderRow {
  cif: string;
  full_name: string;
  email: string;
}

/** Traders as hits: name, then email and CIF; they open the trader's file. */
export function traderHits(rows: readonly TraderRow[]): Hit[] {
  return rows.map((t) => ({ label: `${t.full_name} · ${t.email} · ${t.cif}`, group: "Trader", path: `/traders?cif=${t.cif}`, icon: "traders" }));
}

/** Whether a query is worth a trader lookup: 2 characters or more, not a page name only. */
export function wantsTraders(query: string): boolean {
  return query.trim().length >= 2;
}

/** The label with the parts that match a query word in bold. */
export function highlight(label: string, query: string): ReactNode {
  const wanted = query.toLowerCase().split(/[^a-z0-9@.]+/).filter((w) => w.length > 0);
  if (wanted.length === 0) return label;
  const lower = label.toLowerCase();
  const marks = new Array<boolean>(label.length).fill(false);
  for (const w of wanted) {
    const at = lower.indexOf(w);
    if (at >= 0) for (let i = at; i < at + w.length; i++) marks[i] = true;
  }
  const parts: ReactNode[] = [];
  let i = 0;
  while (i < label.length) {
    const bold = marks[i] === true;
    let j = i;
    while (j < label.length && (marks[j] === true) === bold) j++;
    const text = label.slice(i, j);
    parts.push(bold ? <strong key={i}>{text}</strong> : text);
    i = j;
  }
  return <>{parts}</>;
}

const ORDER: Hit["group"][] = ["Record", "Recent", "Trader", "Page", "Setting"];
const HEADINGS: Record<Hit["group"], string> = { Record: "Reference", Recent: "Recent", Trader: "Traders", Page: "Pages", Setting: "Settings" };

/** Hits grouped under headings, in a fixed group order, keeping each group's ranking. */
export function grouped(hits: readonly Hit[]): Hit[] {
  return ORDER.flatMap((g) => hits.filter((h) => h.group === g));
}

function useTraders(query: string, allowed: boolean): Hit[] {
  const api = useContext(BackOfficeContext)?.api ?? null; // none: pages and settings only
  const [found, setFound] = useState<{ query: string; hits: Hit[] }>({ query: "", hits: [] });
  useEffect(() => {
    if (api === null || !allowed || !wantsTraders(query)) return;
    let live = true;
    const timer = window.setTimeout(() => {
      const q = encodeURIComponent(query.trim());
      api.get<{ traders: TraderRow[] }>(`/staff/traders?q=${q}&limit=${TRADER_LIMIT.toString()}`).then(
        (r) => {
          if (live) setFound({ query, hits: traderHits(r.traders) });
        },
        () => {
          if (live) setFound({ query, hits: [] });
        },
      );
    }, TRADER_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [api, query, allowed]);
  return allowed && found.query === query ? found.hits : [];
}

function Results({ screens, onGo }: { screens: ReadonlySet<string>; onGo: (hit: Hit) => void }) {
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const traders = useTraders(query, screens.has("traders"));
  const hits = grouped([...search(query, screens), ...traders]);
  const chosen = Math.min(at, Math.max(hits.length - 1, 0));
  return (
    <Box sx={{ p: 1.5 }}>
      <TextField
        autoFocus
        fullWidth
        placeholder="Search pages, settings, traders (name, email, CIF) or a reference such as PO00000042"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setAt(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setAt((n) => Math.min(n + 1, hits.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setAt((n) => Math.max(n - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            const hit = hits[chosen];
            if (hit !== undefined) onGo(hit);
          }
        }}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <ICONS.search size={18} strokeWidth={STROKE} aria-hidden="true" />
              </InputAdornment>
            ),
          },
          htmlInput: { "aria-label": "Search the back office" },
        }}
      />
      {query.trim() !== "" && hits.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ p: 1.5 }}>
          Nothing matches. Try fewer letters, a trader's email, or a reference with all 8 digits, for example PO00000042.
        </Typography>
      ) : null}
      <List dense role="listbox" aria-label="Results" sx={{ maxHeight: "55vh", overflowY: "auto" }}>
        {hits.map((h, i) => {
          const Glyph = ICONS[h.icon];
          const head = i === 0 || hits[i - 1]?.group !== h.group;
          return (
            <Fragment key={`${h.group}${h.path}${h.label}`}>
              {head ? (
                <ListSubheader disableSticky sx={{ lineHeight: "28px", bgcolor: "transparent" }}>
                  {HEADINGS[h.group]}
                </ListSubheader>
              ) : null}
              <ListItemButton
                selected={i === chosen}
                role="option"
                aria-selected={i === chosen}
                onMouseEnter={() => {
                  setAt(i);
                }}
                onClick={() => {
                  onGo(h);
                }}
              >
                <ListItemIcon sx={{ minWidth: 36 }}>
                  <Glyph size={SIZE} strokeWidth={STROKE} aria-hidden="true" />
                </ListItemIcon>
                <ListItemText primary={highlight(h.label, query)} />
              </ListItemButton>
            </Fragment>
          );
        })}
      </List>
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.5 }}>
        ↑ ↓ to move · Enter to open · Esc to close
      </Typography>
    </Box>
  );
}

/** Ctrl+K search from any screen (rule AC06), plus a button in the top bar.
It opens near the top of the screen, as a command palette. */
export function CommandSearch() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { permissions } = usePermissions<string[]>();
  const screens = screensFor(permissions ?? []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isOpenKey(e)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  return (
    <>
      <Button
        color="inherit"
        size="small"
        onClick={() => {
          setOpen(true);
        }}
        startIcon={<ICONS.search size={18} strokeWidth={STROKE} aria-hidden="true" />}
        sx={{ textTransform: "none", mr: 1 }}
      >
        Search <Box component="kbd" sx={{ ml: 1, opacity: 0.7, fontSize: 12 }}>Ctrl K</Box>
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        fullWidth
        maxWidth="sm"
        sx={{ "& .MuiDialog-container": { alignItems: "flex-start" } }}
        slotProps={{ paper: { sx: { mt: "10vh" } } }}
      >
        {open ? (
          <Results
            screens={screens}
            onGo={(hit) => {
              setOpen(false);
              if (hit.group !== "Record") rememberHit({ ...hit, group: "Page" });
              void navigate(hit.path);
            }}
          />
        ) : null}
      </Dialog>
    </>
  );
}
