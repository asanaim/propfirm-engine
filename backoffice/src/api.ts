/** The API under one origin (/api). Errors keep the API's code; a step-up
challenge (RFC 9470) becomes `StepUpRequired`. */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly permission: string | null;

  constructor(status: number, code: string | null, message: string, permission: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.permission = permission;
  }
}

export class StepUpRequired extends ApiError {
  constructor(code: string | null, message: string) {
    super(401, code, message);
    this.name = "StepUpRequired";
  }
}

export type TokenSource = () => Promise<string | null>;

function detailOf(body: unknown): { code: string | null; message: string; permission: string | null } {
  if (body !== null && typeof body === "object" && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") return { code: null, message: detail, permission: null };
    if (detail !== null && typeof detail === "object") {
      const d = detail as { code?: unknown; message?: unknown; permission?: unknown };
      return {
        code: typeof d.code === "string" ? d.code : null,
        message: typeof d.message === "string" ? d.message : "request refused",
        permission: typeof d.permission === "string" ? d.permission : null,
      };
    }
  }
  return { code: null, message: "request failed", permission: null };
}

export function isStepUp(header: string | null): boolean {
  return header !== null && header.includes('error="insufficient_user_authentication"');
}

export interface Api {
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, body?: unknown) => Promise<T>;
  put: <T>(path: string, body?: unknown) => Promise<T>;
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
      const { code, message, permission } = detailOf(parsed);
      if (response.status === 401 && isStepUp(response.headers.get("WWW-Authenticate"))) {
        throw new StepUpRequired(code, message);
      }
      throw new ApiError(response.status, code, message, permission);
    }
    return parsed as T;
  }
  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
  };
}
