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
  put: <T>(path: string, body?: unknown) => Promise<T>;
  delete: <T>(path: string) => Promise<T>;
  /** Server-sent events from `path` until the server ends the stream. */
  events: (path: string, onEvent: (name: string, data: unknown) => void, signal: AbortSignal) => Promise<void>;
}

/** Split a server-sent event stream into (event, data) pairs; returns the
unfinished rest of the buffer. Comments (":") are keep-alives. */
export function parseEvents(buffer: string, onEvent: (name: string, data: unknown) => void): string {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let name = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") name = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) onEvent(name, JSON.parse(data.join("\n")) as unknown);
  }
  return rest;
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

const DEVICE_KEY = "pipridge.device";

/** This browser's own random id, kept across visits (D-14.35): the API
stores only its hash, to tell one trader's devices apart. Without storage
(private mode) the API falls back to the browser's user agent. */
export function deviceId(store: Pick<Storage, "getItem" | "setItem"> | null = safeStorage()): string | null {
  if (store === null) return null;
  try {
    const known = store.getItem(DEVICE_KEY);
    if (known !== null && /^[0-9a-f-]{36}$/.test(known)) return known;
    const fresh = crypto.randomUUID();
    store.setItem(DEVICE_KEY, fresh);
    return fresh;
  } catch {
    return null;
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export function createApi(base: string, token: TokenSource, fetcher: typeof fetch = fetch): Api {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const bearer = await token();
    if (bearer !== null) headers.Authorization = `Bearer ${bearer}`;
    const device = bearer === null ? null : deviceId();
    if (device !== null) headers["X-Device-Id"] = device;
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
  async function events(
    path: string,
    onEvent: (name: string, data: unknown) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    const bearer = await token();
    if (bearer !== null) headers.Authorization = `Bearer ${bearer}`;
    const response = await fetcher(`${base}${path}`, { headers, signal });
    if (!response.ok || response.body === null) {
      const text = await response.text();
      const { code, message } = detailOf(text ? (JSON.parse(text) as unknown) : null);
      throw new ApiError(response.status, code, message);
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer = parseEvents(buffer + value, onEvent);
    }
  }
  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    delete: (path) => request("DELETE", path),
    events,
  };
}
