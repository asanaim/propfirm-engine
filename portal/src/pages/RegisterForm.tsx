import { useMemo, useState, type SyntheticEvent } from "react";
import { useApi } from "../api/ApiProvider";
import type { Customer, Registration } from "../api/types";
import { Problem } from "../components/Problem";
import { Button, Card } from "../components/ui";
import { countries } from "../lib/countries";

/** The customer record (CIF): name as on the identity document, date of
birth and country of residence. */
export function RegisterForm({ onDone }: { onDone: (customer: Customer) => void }) {
  const api = useApi();
  const list = useMemo(() => countries(), []);
  const [form, setForm] = useState<Registration>({ full_name: "", date_of_birth: "", residence_country: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Customer>("/me", { ...form, full_name: form.full_name.trim() }));
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Create your trader profile">
      <p className="muted">Use your name as it appears on your identity document.</p>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <label className="field">
          <span>Full name</span>
          <input
            required
            maxLength={200}
            autoComplete="name"
            value={form.full_name}
            onChange={(e) => {
              setForm({ ...form, full_name: e.target.value });
            }}
          />
        </label>
        <label className="field">
          <span>Date of birth</span>
          <input
            required
            type="date"
            autoComplete="bday"
            value={form.date_of_birth}
            onChange={(e) => {
              setForm({ ...form, date_of_birth: e.target.value });
            }}
          />
        </label>
        <label className="field">
          <span>Country of residence</span>
          <select
            required
            autoComplete="country"
            value={form.residence_country}
            onChange={(e) => {
              setForm({ ...form, residence_country: e.target.value });
            }}
          >
            <option value="">Choose a country</option>
            {list.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {error !== null && <Problem error={error} />}
        <div className="card-actions">
          <Button type="submit" busy={busy}>
            Create profile
          </Button>
        </div>
      </form>
    </Card>
  );
}
