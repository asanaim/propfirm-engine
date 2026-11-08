import { Box } from "@mui/material";
import type { ReactNode } from "react";
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

export function Layout({ children }: { children: ReactNode }) {
  return (
    <RaLayout menu={Menu} appBar={BrandBar}>
      {children}
    </RaLayout>
  );
}
