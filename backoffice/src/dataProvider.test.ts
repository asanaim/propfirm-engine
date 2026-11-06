import type { Api } from "./api";
import { dataProvider, page, SOURCES } from "./dataProvider";

function fakeApi(rows: Record<string, unknown>[]): Api & { paths: string[] } {
  const paths: string[] = [];
  return {
    paths,
    get: <T,>(path: string) => {
      paths.push(path);
      return Promise.resolve((path.includes("/PO") ? rows[0] : rows) as T);
    },
    post: () => Promise.reject(new Error("no")),
    put: () => Promise.reject(new Error("no")),
  };
}

const rows = [
  { po: "PO00000002", requested_at: 2, trader_amount_cents: 500 },
  { po: "PO00000001", requested_at: 1, trader_amount_cents: 900 },
  { po: "PO00000003", requested_at: 3, trader_amount_cents: null },
];

describe("dataProvider", () => {
  it("lists with the queue's default filter, the record's id, sorted and paged", async () => {
    const api = fakeApi(rows);
    const dp = dataProvider(api);
    const got = await dp.getList("payouts", {
      filter: {},
      sort: { field: "requested_at", order: "ASC" },
      pagination: { page: 1, perPage: 2 },
    });
    expect(api.paths).toEqual(["/staff/payouts?status=requested"]);
    expect(got.total).toBe(3);
    expect(got.data.map((r: { id: string }) => r.id)).toEqual(["PO00000001", "PO00000002"]);
  });

  it("a filter replaces the default; empty values are dropped", async () => {
    const api = fakeApi(rows);
    await dataProvider(api).getList("payouts", { filter: { status: "paid", x: "" } });
    await dataProvider(api).getList("cases", { filter: { overdue: true } });
    expect(api.paths).toEqual(["/staff/payouts?status=paid", "/staff/cases?overdue=true"]);
  });

  it("reads one record from its own endpoint", async () => {
    const api = fakeApi(rows);
    const got = await dataProvider(api).getOne("payouts", { id: "PO00000002" });
    expect(api.paths).toEqual(["/staff/payouts/PO00000002"]);
    expect((got.data as { id: string }).id).toBe("PO00000002");
  });

  it("refuses generic writes: changes are actions with a reason", async () => {
    const dp = dataProvider(fakeApi(rows));
    await expect(dp.update("payouts", { id: "x", data: {}, previousData: { id: "x" } })).rejects.toThrow("reason");
    await expect(dp.delete("payouts", { id: "x" })).rejects.toThrow("reason");
  });

  it("an unknown list is an error", async () => {
    await expect(dataProvider(fakeApi(rows)).getList("nope", { filter: {} })).rejects.toThrow("no such list");
    expect(Object.keys(SOURCES)).toContain("apprentice-proposals");
  });
});

describe("page", () => {
  it("sorts nulls last in both orders", () => {
    const data = rows.map((r) => ({ ...r, id: r.po }));
    const asc = page(data, { sort: { field: "trader_amount_cents", order: "ASC" }, pagination: { page: 1, perPage: 10 } });
    const desc = page(data, { sort: { field: "trader_amount_cents", order: "DESC" }, pagination: { page: 1, perPage: 10 } });
    expect(asc.map((r) => r.id)).toEqual(["PO00000002", "PO00000001", "PO00000003"]);
    expect(desc.map((r) => r.id)).toEqual(["PO00000001", "PO00000002", "PO00000003"]);
  });
});
