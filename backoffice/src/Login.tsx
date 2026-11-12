import { Box, Button, Typography } from "@mui/material";
import { useCallback, useRef, useState } from "react";
import { useLogin } from "react-admin";
import { Terrain } from "./components/Terrain";

/** The terrain rests this long after the pointer last moved (it holds its
last frame; a screen left open costs no drawing). */
const REST_MS = 3_000;
import { config } from "./config";
import { useThemeMark } from "./Layout";
import { useTilt } from "./motion";

/** No password form here: staff sign in at Keycloak (password and a
second factor), then come back. The screen carries the pipridge look: the
ridge terrain behind a glass card that leans toward the pointer. */
export function Login() {
  const login = useLogin();
  useThemeMark();
  const area = useRef<HTMLElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const [webgl, setWebgl] = useState(true);
  const noWebgl = useCallback(() => {
    setWebgl(false);
  }, []);
  useTilt(card, 5, area);
  return (
    <Box component="main" className="login-stage" ref={area}>
      {webgl ? <Terrain area={area} onUnavailable={noWebgl} idleMs={REST_MS} /> : <div className="menu-ridge-fallback" aria-hidden="true" />}
      <div className="login-card" ref={card}>
        <img className="logo-on-light" src="/brand/logo.svg" alt={config.brand} width="148" height="36" />
        <img className="logo-on-dark" src="/brand/logo-on-dark.svg" alt="" width="148" height="36" />
        <Typography variant="h1" sx={{ fontSize: "1.75rem", fontWeight: 650, letterSpacing: "-0.02em", mt: 3, mb: 1 }}>
          Back office
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          Staff only. Sign in with your password and your authenticator app.
        </Typography>
        <Button variant="contained" size="large" fullWidth onClick={() => void login({})}>
          Sign in
        </Button>
      </div>
    </Box>
  );
}
