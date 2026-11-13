import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type AuditPage, auditVerdict } from "./Reports";
import userEvent from "@testing-library/user-event";
import type { UserManager } from "oidc-client-ts";
import type { ReactNode } from "react";
import { AdminContext, testDataProvider } from "react-admin";
import { ActionButton } from "./ActionButton";
import { StepUpRequired, type Api } from "./api";
import { screensFor } from "./screens";
import { BackOfficeContext } from "./context";
import { Home, linkFor, type StaffPage } from "./Home";

function fakeApi(get: Record<string, unknown>, post: (path: string, body: unknown) => Promise<unknown> = () => Promise.resolve({})) {
  const posted: { path: string; body: unknown }[] = [];
  const api: Api = {
    get: <T,>(path: string) => (path in get ? Promise.resolve(get[path] as T) : Promise.reject(new Error(path))),
    post: <T,>(path: string, body?: unknown) => {
      posted.push({ path, body });
      return post(path, body) as Promise<T>;
    },
    put: () => Promise.reject(new Error("no")),
  };
  return { api, posted };
}

const stepUp = vi.fn(() => Promise.resolve());
const manager = { signinRedirect: stepUp } as unknown as UserManager;

function wrap(api: Api, children: ReactNode) {
  return (
    <AdminContext dataProvider={testDataProvider()}>
      <BackOfficeContext.Provider value={{ api, manager }}>{children}</BackOfficeContext.Provider>
    </AdminContext>
  );
}

describe("screensFor", () => {
  it("shows each login only the screens its permissions reach", () => {
    const apprentice = screensFor(["staff.view_masked", "apprentice.propose"]);
    expect(apprentice.has("cases")).toBe(true);
    expect(apprentice.has("members")).toBe(false);
    expect(apprentice.has("proposals")).toBe(false);
    const admin = screensFor(["staff.view", "staff.directory", "settings.view", "news.desk"]);
    for (const s of ["members", "grants", "cutoffs", "proposals", "changes", "anomalies", "audit", "news"]) {
      expect(admin.has(s)).toBe(true);
    }
    expect(screensFor([]).has("payouts")).toBe(false);
    expect(screensFor(["staff.view"]).has("audit")).toBe(false);
  });
});

describe("auditVerdict", () => {
  const page = (status: AuditPage["status"], head = 10): AuditPage => ({ head, status, records: [] });
  it("says whether the log is intact, unchecked or tampered with", () => {
    expect(auditVerdict(page(null)).tone).toBe("warning");
    const ok = auditVerdict(page({ checked_at: 0, verified_seq: 8, anchored_seq: 5, broken_seq: null, broken_reason: null }));
    expect(ok.tone).toBe("success");
    expect(ok.text).toContain("8 records checked");
    expect(ok.text).toContain("2 newer");
    const bad = auditVerdict(page({ checked_at: 0, verified_seq: 8, anchored_seq: 5, broken_seq: 3, broken_reason: "edited" }));
    expect(bad.tone).toBe("error");
    expect(bad.text).toContain("record 3");
  });
});

describe("linkFor", () => {
  it("sends each to-do to the record's screen", () => {
    expect(linkFor({ kind: "payout_authorise", ref: "PO00000001" })).toBe("/payouts/PO00000001/show");
    expect(linkFor({ kind: "case_decision", ref: "CAS00000001" })).toBe("/cases/CAS00000001/show");
    expect(linkFor({ kind: "settings_proposal", ref: "4" })).toBe("/proposals");
    expect(linkFor({ kind: "training", ref: "kyc" })).toBeNull();
  });
});

describe("ActionButton", () => {
  it("sends the action with the reason typed", async () => {
    const { api, posted } = fakeApi({});
    render(wrap(api, <ActionButton label="Deny" path="/staff/payouts/PO00000001/deny" />));
    await userEvent.click(screen.getByRole("button", { name: "Deny" }));
    const send = screen.getAllByRole("button", { name: "Deny" }).at(-1);
    expect(send).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText(/Reason/), "wallet mismatch");
    await userEvent.click(send as HTMLElement);
    await waitFor(() => {
      expect(posted).toEqual([{ path: "/staff/payouts/PO00000001/deny", body: { reason: "wallet mismatch" } }]);
    });
  });

  it("offers a fresh second factor when the API asks for one", async () => {
    const { api } = fakeApi({}, () => Promise.reject(new StepUpRequired("mfa_required", "again")));
    render(wrap(api, <ActionButton label="Approve" path="/staff/grants/GRT00000001/approve" />));
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await userEvent.type(screen.getByLabelText(/Reason/), "ok");
    await userEvent.click(screen.getAllByRole("button", { name: "Approve" }).at(-1) as HTMLElement);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm it is you" }));
    expect(stepUp).toHaveBeenCalledWith(expect.objectContaining({ max_age: 0 }));
  });
});

const me: StaffPage = {
  access: { sid: "STF00000001", tier: "T2", title: "Specialist", permissions: [], sections: { payouts: "T2", kyc: "T1" } },
  member: { name: "Rina", email: null },
  todo: [{ kind: "payout_pass", ref: "PO00000001", title: "Pass payout PO00000001 (100.00 USD)", since: 0, due_at: null }],
  duties: [{ sheet: "payouts", title: "Payouts desk", version: 2, duties: ["Check the wallet"], acknowledged: false }],
  notifications: [{ id: 7, kind: "escalation", ref: "PO00000001", title: "Not acknowledged", body: "b", urgent: true, ts: 0 }],
  training: [{ section: "kyc", title: "KYC basics", status: "missing", valid_until: null }],
};

describe("Home", () => {
  it("shows access, to-do, notifications, duties and training; acknowledges", async () => {
    const { api, posted } = fakeApi({ "/staff/me": me });
    render(wrap(api, <Home />));
    expect(await screen.findByText("Rina")).toBeInTheDocument();
    expect(screen.getByText("payouts · level T2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Pass payout PO00000001/ })).toHaveAttribute("href", "#/payouts/PO00000001/show");
    expect(screen.getByText(/Urgent · Not acknowledged/)).toBeInTheDocument();
    expect(screen.getByText(/approvals blocked/)).toBeInTheDocument();
    expect(screen.getByText("Training blocking approvals").previousSibling).toHaveTextContent("1");
    await userEvent.click(screen.getByRole("button", { name: "I have read these duties" }));
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(posted.map((p) => p.path)).toEqual(["/staff/me/duties/payouts/ack", "/staff/notifications/7/ack"]);
  });
});

describe("My notes", () => {
  it("adds a note with a due time and ticks one off", async () => {
    const withNote = { ...me, todo: [...me.todo, { kind: "note", ref: "7", title: "Call the auditor", since: 0, due_at: null }] };
    const { api, posted } = fakeApi({ "/staff/me": withNote });
    render(wrap(api, <Home />));
    expect(await screen.findByText("Call the auditor")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Add a note"), "Check payout PO1");
    fireEvent.change(screen.getByLabelText("Due (optional)"), { target: { value: "2030-01-02T09:30" } });
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await userEvent.click(screen.getByRole("button", { name: "Done: Call the auditor" }));
    expect(posted[0]).toEqual({ path: "/staff/me/notes", body: { text: "Check payout PO1", due_at: new Date("2030-01-02T09:30").getTime() } });
    expect(posted[1]?.path).toBe("/staff/me/notes/7/done");
  });
});
