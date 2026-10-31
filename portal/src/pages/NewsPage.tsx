import { useEffect, useState } from "react";
import { useApi } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { Problem } from "../components/Problem";
import { Button, Card, Loading, ScrollRegion } from "../components/ui";

export interface NewsEvent {
  event_id: string;
  start_ts: number;
  end_ts: number; // the window is [start_ts, end_ts)
  symbols: string[];
  title: string | null; // from the firm's calendar or the risk desk
}

/** Restricted events not over yet (M13): no trade opens or closes on the
listed symbols inside the window. */
export function NewsPage() {
  const api = useApi();
  const auth = useAuth();
  const [events, setEvents] = useState<NewsEvent[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (auth.status !== "signed_in") return;
    api.get<NewsEvent[]>("/news").then(setEvents, setError);
  }, [api, auth.status]);
  if (auth.status !== "signed_in") {
    return <Button onClick={() => void auth.signIn("/news")}>Sign in to see the news calendar</Button>;
  }
  if (error !== null) return <Problem error={error} />;
  if (events === null) return <Loading />;
  const time = (ms: number) =>
    new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "long" }); // long: with the time zone
  return (
    <div className="page">
      <h1>News calendar</h1>
      <p className="lead">On funded accounts, no trade opens or closes on the listed symbols inside these windows.</p>
      <p className="muted">
        Each window runs from 2 minutes before to 2 minutes after the release. The list comes from the publishers&apos;
        own schedules (BLS, Federal Reserve, ECB, Bank of England, EIA) and is updated monthly. Bank of Japan decisions
        have no fixed time: their window opens when the statement is published and runs 2 minutes. Crypto is not
        restricted.
      </p>
      {events.length === 0 ? (
        <Card>
          <p>No restricted events are scheduled.</p>
        </Card>
      ) : (
        <ScrollRegion label="Restricted news events">
          <table>
            <caption className="visually-hidden">Restricted news events</caption>
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">From</th>
                <th scope="col">Until</th>
                <th scope="col">Symbols</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id}>
                  <th scope="row">{e.title ?? e.event_id}</th>
                  <td>{time(e.start_ts)}</td>
                  <td>{time(e.end_ts - 1)}</td>
                  <td className="wrap">{e.symbols.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollRegion>
      )}
    </div>
  );
}
