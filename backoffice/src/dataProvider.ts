import type { DataProvider, GetListParams, RaRecord } from "react-admin";
import type { Api } from "./api";

/** Each back-office list the API offers, read-only for react-admin: every
change is an action of its own (pass, authorise, approve...), sent with a
reason by the record's buttons, never a generic update. */
export interface Source {
  list: (filter: Record<string, unknown>) => string;
  one?: (id: string) => string;
  id: string; // the record's id field
  defaults?: Record<string, string>;
}

const q = (filter: Record<string, unknown>, defaults: Record<string, string> = {}): string => {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...defaults, ...filter })) {
    if (v !== undefined && v !== null && v !== "") params.set(k, typeof v === "string" ? v : JSON.stringify(v));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
};

export const SOURCES: Record<string, Source> = {
  payouts: { list: (f) => `/staff/payouts${q(f, { status: "requested" })}`, one: (id) => `/staff/payouts/${id}`, id: "po" },
  cases: { list: (f) => `/staff/cases${q(f)}`, one: (id) => `/staff/cases/${id}`, id: "cas" },
  onboardings: {
    list: (f) => `/staff/onboardings${q(f, { status: "review" })}`,
    one: (id) => `/staff/onboardings/${id}`,
    id: "onb",
  },
  progressions: {
    list: (f) => `/staff/progressions${q(f, { status: "review" })}`,
    one: (id) => `/staff/progressions/${id}`,
    id: "prg",
  },
  members: { list: (f) => `/staff/members${q(f)}`, one: (id) => `/staff/members/${id}`, id: "sid" },
  grants: { list: (f) => `/staff/grants${q(f, { status: "requested" })}`, id: "grt" },
  proposals: { list: (f) => `/admin/settings/proposals${q(f)}`, id: "id" },
  changes: { list: () => "/admin/settings/changes", id: "id" },
  cutoffs: { list: () => "/staff/cutoffs", id: "sid" },
  "apprentice-proposals": { list: () => "/staff/apprentice/proposals", id: "id" },
  notifications: { list: (f) => `/staff/notifications${q(f)}`, id: "id" },
};

function source(resource: string): Source {
  const found = SOURCES[resource];
  if (found === undefined) throw new Error(`no such list: ${resource}`);
  return found;
}

function withId(row: Record<string, unknown>, key: string): RaRecord {
  const id = row[key];
  return { ...row, id: typeof id === "string" ? id : JSON.stringify(id) };
}

/** Sorting and paging in the browser: the API returns each queue whole
(at most a few hundred rows). */
export function page(rows: RaRecord[], params: Pick<GetListParams, "pagination" | "sort">): RaRecord[] {
  const { field, order } = params.sort ?? { field: "id", order: "ASC" };
  const sorted = [...rows].sort((a, b) => {
    const x = a[field] as string | number | null | undefined;
    const y = b[field] as string | number | null | undefined;
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (x < y ? -1 : 1) * (order === "DESC" ? -1 : 1);
  });
  const { page: n, perPage } = params.pagination ?? { page: 1, perPage: 25 };
  return sorted.slice((n - 1) * perPage, n * perPage);
}

const readOnly = (): Promise<never> => Promise.reject(new Error("changes are actions with a reason"));

export function dataProvider(api: Api): DataProvider {
  async function all(resource: string, filter: Record<string, unknown>): Promise<RaRecord[]> {
    const s = source(resource);
    const rows = await api.get<Record<string, unknown>[]>(s.list(filter));
    return rows.map((r) => withId(r, s.id));
  }
  return {
    async getList(resource, params) {
      const rows = await all(resource, params.filter as Record<string, unknown>);
      return { data: page(rows, params) as never[], total: rows.length };
    },
    async getOne(resource, params) {
      const s = source(resource);
      const id = String(params.id);
      if (s.one !== undefined) {
        return { data: withId(await api.get<Record<string, unknown>>(s.one(id)), s.id) as never };
      }
      const found = (await all(resource, {})).find((r) => r.id === id);
      if (found === undefined) throw new Error(`${id} not found`);
      return { data: found as never };
    },
    async getMany(resource, params) {
      const rows = await all(resource, {});
      const ids = new Set(params.ids.map(String));
      return { data: rows.filter((r) => ids.has(String(r.id))) as never[] };
    },
    async getManyReference(resource, params) {
      const rows = (await all(resource, {})).filter((r) => r[params.target] === params.id);
      return { data: page(rows, params) as never[], total: rows.length };
    },
    create: readOnly,
    update: readOnly,
    updateMany: readOnly,
    delete: readOnly,
    deleteMany: readOnly,
  };
}
