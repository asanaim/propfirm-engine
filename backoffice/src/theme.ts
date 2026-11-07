import { defaultDarkTheme, defaultLightTheme, type RaThemeOptions } from "react-admin";

/** The brand (brand/), as on the trader site: ink #0F2B33, the pip amber
#F2A93B, paper #F8F7F3. Light: ink buttons with paper text; dark: amber
buttons with ink text. Both pairs far above WCAG 2.2 AA. The top bar is ink
in both themes (it carries the reverse logo). */
const INK = "#0f2b33";
const PAPER = "#f8f7f3";
const BRAND = { light: "#1a4651", main: INK, dark: "#0b2128", contrastText: PAPER };
const BRAND_DARK = { light: "#f5bd66", main: "#f2a93b", dark: "#d98f22", contrastText: INK };

const FONT = '"Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", sans-serif';

// react-admin types its own defaults as MUI ThemeOptions, whose optional
// `components` does not satisfy RaThemeOptions under exactOptionalPropertyTypes.
// The empty-list message is drawn in text.disabled (below 4.5:1): use the
// secondary text colour instead.
function components(dark: boolean, base: object) {
  const border = dark ? "#23434c" : "#e3e1d9";
  const shadow = dark
    ? "0 1px 2px rgb(0 0 0 / 40%), 0 8px 24px rgb(0 0 0 / 28%)"
    : "0 1px 2px rgb(15 43 51 / 6%), 0 8px 24px rgb(15 43 51 / 7%)";
  return {
    ...base,
    RaEmpty: {
      styleOverrides: {
        root: { "& .RaEmpty-message": { color: dark ? "rgba(255, 255, 255, 0.7)" : "rgba(0, 0, 0, 0.6)" } },
      },
    },
    RaAppBar: {
      styleOverrides: {
        root: {
          "& .RaAppBar-toolbar": { minHeight: 60 },
          "& .MuiAppBar-root, &.MuiAppBar-root": {
            backgroundColor: "rgb(15 43 51 / 92%)",
            backdropFilter: "saturate(180%) blur(20px)",
            color: PAPER,
            boxShadow: "none",
            borderBottom: "1px solid rgb(248 247 243 / 8%)",
          },
        },
      },
    },
    RaMenuItemLink: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          margin: "2px 8px",
          "&.RaMenuItemLink-active": {
            backgroundColor: dark ? "rgb(242 169 59 / 14%)" : "rgb(15 43 51 / 8%)",
            fontWeight: 600,
          },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        rounded: { borderRadius: 16 },
        elevation1: { boxShadow: shadow, border: `1px solid ${border}` },
      },
    },
    MuiCard: { styleOverrides: { root: { borderRadius: 16 } } },
    MuiButton: {
      styleOverrides: {
        root: { textTransform: "none" as const, borderRadius: 999, fontWeight: 600, letterSpacing: 0 },
      },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
    MuiTableCell: {
      styleOverrides: {
        head: { fontWeight: 650, color: dark ? "#a7bbc0" : "#4b5d63" },
        root: { borderColor: border },
      },
    },
    MuiAlert: { styleOverrides: { root: { borderRadius: 12 } } },
  };
}

const typography = {
  fontFamily: FONT,
  h1: { fontWeight: 720, letterSpacing: "-0.03em" },
  h2: { fontWeight: 700, letterSpacing: "-0.02em" },
  h5: { fontWeight: 700, letterSpacing: "-0.01em" },
  h6: { fontWeight: 680, letterSpacing: "-0.01em" },
  button: { fontWeight: 600 },
};

export const lightTheme = {
  ...defaultLightTheme,
  typography,
  shape: { borderRadius: 12 },
  components: components(false, defaultLightTheme.components ?? {}),
  palette: {
    ...defaultLightTheme.palette,
    primary: BRAND,
    secondary: BRAND,
    background: { default: PAPER, paper: "#ffffff" },
    text: { primary: INK, secondary: "#4b5d63" },
  },
} as RaThemeOptions;

export const darkTheme = {
  ...defaultDarkTheme,
  typography,
  shape: { borderRadius: 12 },
  components: components(true, defaultDarkTheme.components ?? {}),
  palette: {
    ...defaultDarkTheme.palette,
    primary: BRAND_DARK,
    secondary: BRAND_DARK,
    background: { default: "#061519", paper: INK },
    text: { primary: PAPER, secondary: "#a7bbc0" },
  },
} as RaThemeOptions;
