import { Box, Button, Dialog, InputAdornment, List, ListItemButton, ListItemIcon, ListItemText, TextField, Typography } from "@mui/material";
import { useEffect, useState } from "react";
import { usePermissions } from "react-admin";
import { useNavigate } from "react-router-dom";
import { ICONS, SIZE, STROKE } from "./icons";
import { type Hit, search } from "./places";
import { screensFor } from "./screens";

/** Whether a key press is Ctrl+K (or Cmd+K on a Mac). */
export function isOpenKey(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k";
}

function Results({ screens, onGo }: { screens: ReadonlySet<string>; onGo: (hit: Hit) => void }) {
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const hits = search(query, screens);
  const chosen = Math.min(at, Math.max(hits.length - 1, 0));
  return (
    <Box sx={{ p: 1.5 }}>
      <TextField
        autoFocus
        fullWidth
        size="small"
        placeholder="Go to a page or setting, or type a reference (PO, CAS, ONB, PRG, CIF)"
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
          Nothing matches. A reference needs its letters and all 8 digits, for example PO00000042.
        </Typography>
      ) : null}
      <List dense role="listbox" aria-label="Results">
        {hits.map((h, i) => {
          const Glyph = ICONS[h.icon];
          return (
            <ListItemButton
              key={`${h.group}${h.path}${h.label}`}
              selected={i === chosen}
              role="option"
              aria-selected={i === chosen}
              onClick={() => {
                onGo(h);
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Glyph size={SIZE} strokeWidth={STROKE} aria-hidden="true" />
              </ListItemIcon>
              <ListItemText primary={h.label} secondary={h.group} />
            </ListItemButton>
          );
        })}
      </List>
    </Box>
  );
}

/** Ctrl+K search from any screen (rule AC06), plus a button in the top bar. */
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
      >
        {open ? (
          <Results
            screens={screens}
            onGo={(hit) => {
              setOpen(false);
              void navigate(hit.path);
            }}
          />
        ) : null}
      </Dialog>
    </>
  );
}
