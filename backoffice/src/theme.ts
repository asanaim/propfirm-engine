import { defaultDarkTheme, defaultLightTheme, type RaThemeOptions } from "react-admin";

/** The brand (brand/): ink #0F2B33 with paper text in light; the pip amber
#F2A93B with ink text in dark. Both pairs far above WCAG 2.2 AA. */
const BRAND = { light: "#1a4651", main: "#0f2b33", dark: "#0b2128", contrastText: "#f8f7f3" };
const BRAND_DARK = { light: "#f5bd66", main: "#f2a93b", dark: "#d98f22", contrastText: "#0f2b33" };

// react-admin types its own defaults as MUI ThemeOptions, whose optional
// `components` does not satisfy RaThemeOptions under exactOptionalPropertyTypes
// the empty-list message is drawn in text.disabled (below 4.5:1): use the
// secondary text colour instead
const empty = (color: string) => ({ RaEmpty: { styleOverrides: { root: { "& .RaEmpty-message": { color } } } } });

export const lightTheme = {
  ...defaultLightTheme,
  components: { ...defaultLightTheme.components, ...empty("rgba(0, 0, 0, 0.6)") },
  palette: { ...defaultLightTheme.palette, primary: BRAND, secondary: BRAND },
} as RaThemeOptions;

export const darkTheme = {
  ...defaultDarkTheme,
  components: { ...defaultDarkTheme.components, ...empty("rgba(255, 255, 255, 0.7)") },
  palette: { ...defaultDarkTheme.palette, primary: BRAND_DARK, secondary: BRAND_DARK },
} as RaThemeOptions;
