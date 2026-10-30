import { useAuth } from "../auth/AuthProvider";
import { Button, Card, Notice } from "../components/ui";

export function SecurityPage() {
  const auth = useAuth();
  const session = auth.session;
  if (session === null) {
    return (
      <div className="page">
        <h1>Security</h1>
        <Button onClick={() => void auth.signIn("/security")}>Sign in</Button>
      </div>
    );
  }
  const when = session.authTime === null ? null : new Date(session.authTime).toLocaleString();
  return (
    <div className="page narrow">
      <h1>Security</h1>
      <Card
        title="Two-factor authentication"
        actions={
          <Button variant="secondary" onClick={() => void auth.setUpAuthenticator("/security")}>
            Set up an authenticator app
          </Button>
        }
      >
        <p>
          An authenticator app adds a six-digit code to your sign-in. It is optional for signing in, and required to
          change your payout wallet or to request a payout.
        </p>
        {session.mfa ? (
          <Notice tone="ok">This sign-in used your authenticator code.</Notice>
        ) : (
          <Notice>This sign-in used your password only.</Notice>
        )}
      </Card>
      <Card title="This sign-in">
        <dl className="facts">
          <dt>Email</dt>
          <dd>{session.email ?? "—"}</dd>
          <dt>Signed in</dt>
          <dd>{when ?? "—"}</dd>
        </dl>
      </Card>
    </div>
  );
}
