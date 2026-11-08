import { Box, useTheme } from "@mui/material";
import { type ReactNode, useEffect } from "react";
import { AppBar, Layout as RaLayout, TitlePortal } from "react-admin";
import { CommandSearch } from "./CommandSearch";
import { Menu } from "./Menu";

/** The top bar: the pipridge logo (the reverse logo: the bar is ink in both
themes), "Back office", the page's title, then the Ctrl+K search (AC06). */
export function BrandBar() {
  return (
    <AppBar color="inherit" className="brand-bar">
      <Box component="a" href="#/" aria-label="pipridge back office home" sx={{ display: "flex", alignItems: "center", gap: 1.25, mr: 2.5 }}>
        <img src="/brand/logo-on-dark.svg" alt="" width={124} height={30} style={{ display: "block", height: 30, width: "auto" }} />
        <Box component="span" className="brand-bar-label">
          Back office
        </Box>
      </Box>
      <TitlePortal variant="h6" className="brand-bar-title" />
      <CommandSearch />
    </AppBar>
  );
}

/** Mirrors the MUI theme's mode on <html data-theme>, so CSS and the
terrain pick the colours of the theme in use. */
export function useThemeMark(): void {
  const mode = useTheme().palette.mode;
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
  }, [mode]);
}

export function Layout({ children }: { children: ReactNode }) {
  useThemeMark();
  return (
    <RaLayout menu={Menu} appBar={BrandBar}>
      {children}
    </RaLayout>
  );
}
