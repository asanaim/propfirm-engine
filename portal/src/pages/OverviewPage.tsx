import { useMe } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { Button, Card, Loading, Notice } from "../components/ui";
import { Link } from "../router";
import { RegisterForm } from "./RegisterForm";

export function OverviewPage() {
  const auth = useAuth();
  const me = useMe();
  if (auth.status === "loading" || me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page hero">
        <h1>Pass the evaluation. Earn 80 % of the profit.</h1>
        <p className="lead">Two-step, one-step or instant funding, on simulated accounts with real rewards.</p>
        <div className="row">
          <Button onClick={() => void auth.signIn("/")}>Sign in</Button>
          <Link className="button button-secondary" href="/shop">
            See the challenges
          </Link>
        </div>
      </div>
    );
  }
  if (me.status === "unregistered") {
    return (
      <div className="page narrow">
        <h1>Welcome</h1>
        <RegisterForm onDone={me.reload} />
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const { full_name, accounts, orders } = me.me;
  const pending = orders.filter((o) => o.status === "pending").length;
  return (
    <div className="page">
      <h1>Hello, {full_name}</h1>
      <div className="grid">
        <Card title="Accounts" actions={<Link href="/accounts">View accounts</Link>}>
          <p className="figure">{accounts.length}</p>
          <p className="muted">trading {accounts.length === 1 ? "account" : "accounts"}</p>
        </Card>
        <Card title="Orders" actions={<Link href="/shop">New challenge</Link>}>
          <p className="figure">{orders.length}</p>
          <p className="muted">{pending > 0 ? `${pending} awaiting payment` : "all settled"}</p>
        </Card>
        <Card title="Security" actions={<Link href="/security">Security settings</Link>}>
          <p>{auth.session?.mfa ? "Signed in with two-factor authentication." : "Two-factor authentication is off for this sign-in."}</p>
        </Card>
      </div>
    </div>
  );
}
