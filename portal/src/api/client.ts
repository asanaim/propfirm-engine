/** The API, under one origin (/api). Errors keep the API's code. */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** The action needs a second factor entered recently (RFC 9470): sign in
again, within `maxAge` seconds when given. */
export class StepUpRequired extends ApiError {
  readonly maxAge: number | null;

  constructor(code: string | null, message: string, maxAge: number | null) {
    super(401, code, message);
    this.name = "StepUpRequired";
    this.maxAge = maxAge;
  }
}

export type TokenSource = () => Promise<string | null>;

export interface Api {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
}

function detailOf(body: unknown): { code: string | null; message: string } {
  if (body !== null && typeof body === "object" && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") return { code: null, message: detail };
    if (detail !== null && typeof detail === "object") {
      const d = detail as { code?: unknown; message?: unknown };
      return {
        code: typeof d.code === "string" ? d.code : null,
        message: typeof d.message === "string" ? d.message : "request refused",
      };
    }
  }
  return { code: null, message: "request failed" };
}

export function parseStepUp(header: string | null): { maxAge: number | null } | null {
  if (header === null || !/error="insufficient_user_authentication"/.test(header)) return null;
  const age = /max_age=(\d+)/.exec(header);
  return { maxAge: age?.[1] === undefined ? null : Number(age[1]) };
}

export function createApi(base: string, token: TokenSource, fetcher: typeof fetch = fetch): Api {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const bearer = await token();
    if (bearer !== null) headers.Authorization = `Bearer ${bearer}`;
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetcher(`${base}${path}`, init);
    const text = await response.text();
    const parsed: unknown = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const { code, message } = detailOf(parsed);
      const stepUp = response.status === 401 ? parseStepUp(response.headers.get("WWW-Authenticate")) : null;
      if (stepUp !== null) throw new StepUpRequired(code, message, stepUp.maxAge);
      throw new ApiError(response.status, code, message);
    }
    return parsed as T;
  }
  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
  };
}
