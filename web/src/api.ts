export type Row = Record<string, any>;
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public details: any,
    public fields: any[] = [],
  ) {
    super(code);
  }
}
let csrf = "";
export const setCSRF = (value: string) => {
  csrf = value;
};
export async function api(
  path: string,
  body?: any,
  method?: string,
  key?: string,
  csrfOverride?: string,
): Promise<any> {
  const headers: Record<string, string> = {};
  const verb = method || (body === undefined ? "GET" : "POST");
  if (verb !== "GET") {
    headers["X-CSRF-Token"] = csrfOverride ?? csrf;
    headers["Idempotency-Key"] = key || crypto.randomUUID();
    if (!(body instanceof FormData) && !(body instanceof Blob))
      headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(path, {
      method: verb,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body:
        body === undefined
          ? undefined
          : body instanceof FormData || body instanceof Blob
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "offline", null);
  }
  if (!response.ok) {
    if ((response.status === 401 || response.status === 403) && path !== '/api/auth/session')
      window.dispatchEvent(new Event('authority-check'));
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      data.error?.code || "request_failed",
      data.error?.details,
      data.error?.fields,
    );
  }
  if (response.status === 204) return {};
  return response.json();
}
export async function fileBlob(path: string) {
  const r = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new ApiError(
      r.status,
      e.error?.code || "file_unavailable",
      e.error?.details,
    );
  }
  return r.blob();
}
export async function upload(file: File) {
  const data = new FormData();
  const current = await api('/api/me');
  data.append("file", file);
  data.append('owner_type','user');
  data.append('owner_id',current.id);
  return api("/api/attachments", data);
}
export const rows = (data: any): Row[] =>
  Array.isArray(data) ? data : data?.items || data?.rows || data?.records || [];
export function query(values: Row) {
  return new URLSearchParams(
    Object.entries(values)
      .filter(([, v]) => v !== "" && v != null)
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
export async function download(path: string, name: string) {
  const blob = await fileBlob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
