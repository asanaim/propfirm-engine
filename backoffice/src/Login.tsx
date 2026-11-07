import { Box, Button, Card, CardContent, Typography } from "@mui/material";
import { useLogin } from "react-admin";
import { config } from "./config";

/** No password form here: staff sign in at Keycloak (password and a
second factor), then come back. */
export function Login() {
  const login = useLogin();
  return (
    <Box component="main" sx={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <Card sx={{ minWidth: 320 }}>
        <CardContent>
          <img src="/brand/logo.svg" alt={config.brand} width="148" height="36" style={{ display: "block", marginBottom: 16 }} />
          <Typography variant="h1" sx={{ fontSize: "1.5rem", mb: 2 }}>
            Back office
          </Typography>
          <Typography sx={{ mb: 2 }}>Staff only. You will need your authenticator app.</Typography>
          <Button variant="contained" onClick={() => void login({})}>
            Sign in
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
}
