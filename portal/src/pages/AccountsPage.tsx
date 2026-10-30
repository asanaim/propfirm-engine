import { useMe } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { Button, Card, Loading, Notice } from "../components/ui";
import { formatCents, formatDate, formatSize } from "../lib/money";
import { Link } from "../router";

const STAGES: Record<string, string> = { s1: "Stage 1", s2: "Stage 2", funded: "Funded" };
const ORDER_STATUS: Record<string, string> = {
  pending: "Awaiting payment",
  paid: "Paid",
  provisioned: "Account open",
  failed: "Failed",
};

export function AccountsPage() {
  const auth = useAuth();
  const me = useMe();
  if (me.status === "loading") return <Loading />;
  if (me.status === "anonymous") {
    return (
      <div className="page">
        <h1>Accounts</h1>
        <Button onClick={() => void auth.signIn("/accounts")}>Sign in to see your accounts</Button>
      </div>
    );
  }
  if (me.status === "unregistered") {
    return (
      <div className="page">
        <h1>Accounts</h1>
        <Notice>
          Create your trader profile first on the <Link href="/">overview</Link>.
        </Notice>
      </div>
    );
  }
  if (me.status === "error") return <Notice tone="error">{me.error}</Notice>;
  const { accounts, orders } = me.me;
  return (
    <div className="page">
      <h1>Accounts</h1>
      {accounts.length === 0 ? (
        <Card>
          <p>You have no trading accounts yet.</p>
          <Link className="button button-primary" href="/shop">
            Choose a challenge
          </Link>
        </Card>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="visually-hidden">Your trading accounts</caption>
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col">Product</th>
                <th scope="col">Size</th>
                <th scope="col">Stage</th>
                <th scope="col">Opened</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.acc}>
                  <th scope="row">{a.acc}</th>
                  <td>{a.product_id}</td>
                  <td>{formatSize(a.size_cents)}</td>
                  <td>{STAGES[a.stage] ?? a.stage}</td>
                  <td>{formatDate(a.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {orders.length > 0 && (
        <>
          <h2>Orders</h2>
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Your orders</caption>
              <thead>
                <tr>
                  <th scope="col">Order</th>
                  <th scope="col">Product</th>
                  <th scope="col">Price</th>
                  <th scope="col">Status</th>
                  <th scope="col">Date</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.ord}>
                    <th scope="row">{o.ord}</th>
                    <td>
                      {formatSize(o.size_cents)} {o.product_id}
                    </td>
                    <td>{formatCents(o.price_cents)}</td>
                    <td>{ORDER_STATUS[o.status] ?? o.status}</td>
                    <td>{formatDate(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
