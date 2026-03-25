export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

interface ApiOptions extends RequestInit {
  params?: Record<string, string>;
}

export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {}
): Promise<T | null> {
  const { params, ...fetchOptions } = options;

  let url = `${API_BASE}${path}`;
  if (params) {
    const searchParams = new URLSearchParams(params);
    url += `?${searchParams.toString()}`;
  }

  const res = await fetch(url, {
    ...fetchOptions,
    headers: {
      ...(fetchOptions.body ? { "Content-Type": "application/json" } : {}),
      ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
      ...fetchOptions.headers,
    },
  });

  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(
      res.status,
      data?.detail || `API error: ${res.status}`,
      data
    );
  }

  if (res.status === 204) return null;
  return res.json();
}

// Convenience methods
export const apiGet = <T>(path: string, params?: Record<string, string>) =>
  api<T>(path, { params });

export const apiPost = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });

export const apiPut = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "PUT",
    body: body ? JSON.stringify(body) : undefined,
  });

export const apiDelete = <T = null>(path: string) =>
  api<T>(path, { method: "DELETE" });
