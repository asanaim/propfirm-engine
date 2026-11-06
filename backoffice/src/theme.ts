import { defaultDarkTheme, defaultLightTheme, type RaThemeOptions } from "react-admin";

/** The firm's blue (the portal's --accent), at WCAG 2.2 AA contrast with
its text: #2952cc on white 6.6:1; #8aa8ff under #0e1013 8.6:1. */
const BLUE = { light: "#5a7be0", main: "#2952cc", dark: "#1f43ad", contrastText: "#ffffff" };
const BLUE_DARK = { light: "#a9c0ff", main: "#8aa8ff", dark: "#6f8fe6", contrastText: "#0e1013" };

// react-admin types its own defaults as MUI ThemeOptions, whose optional
// `components` does not satisfy RaThemeOptions under exactOptionalPropertyTypes
// the empty-list message is drawn in text.disabled (below 4.5:1): use the
// secondary text colour instead
const empty = (color: string) => ({ RaEmpty: { styleOverrides: { root: { "& .RaEmpty-message": { color } } } } });

export const lightTheme = {
  ...defaultLightTheme,
  components: { ...defaultLightTheme.components, ...empty("rgba(0, 0, 0, 0.6)") },
  palette: { ...defaultLightTheme.palette, primary: BLUE, secondary: BLUE },
} as RaThemeOptions;

export const darkTheme = {
  ...defaultDarkTheme,
  components: { ...defaultDarkTheme.components, ...empty("rgba(255, 255, 255, 0.7)") },
  palette: { ...defaultDarkTheme.palette, primary: BLUE_DARK, secondary: BLUE_DARK },
} as RaThemeOptions;
