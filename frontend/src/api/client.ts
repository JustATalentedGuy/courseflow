const configuredApiUrl =
  import.meta.env.VITE_API_URL ??
  import.meta.env.VITE_API_BASE_URL ??
  "http://localhost:8000";
const normalizedApiUrl = configuredApiUrl.replace(/\/+$/, "");

export const API_BASE_URL = normalizedApiUrl.endsWith("/api/v1")
  ? normalizedApiUrl
  : `${normalizedApiUrl}/api/v1`;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const ACCESS_TOKEN_KEY = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";
let refreshPromise: Promise<boolean> | null = null;

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken?: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  if (refreshToken) {
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  }
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

async function attemptTokenRefresh(): Promise<boolean> {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) return false;

  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) {
    clearTokens();
    return false;
  }
  const body = (await response.json()) as { access_token: string };
  setTokens(body.access_token);
  return true;
}

async function refreshOnce(): Promise<boolean> {
  refreshPromise ??= attemptTokenRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

export async function request<T>(
  path: string,
  options: RequestInit & {
    accessToken?: string;
    skipRefresh?: boolean;
    responseType?: "json" | "text" | "blob";
  } = {},
  hasRetried = false,
): Promise<T> {
  const {
    accessToken,
    skipRefresh,
    responseType,
    ...fetchOptions
  } = options;
  const headers = new Headers(fetchOptions.headers);
  if (fetchOptions.body) headers.set("Content-Type", "application/json");

  const token = accessToken || getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...fetchOptions,
    headers,
  });

  if (response.status === 401 && !skipRefresh && !hasRetried) {
    if (await refreshOnce()) {
      return request<T>(path, { ...options, accessToken: getAccessToken() ?? undefined }, true);
    }
    if (window.location.pathname !== "/login") window.location.assign("/login");
    throw new ApiError(401, "Unauthorised");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? "Request failed");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (responseType === "blob") {
    return response.blob() as Promise<T>;
  }
  if (responseType === "text") {
    return response.text() as Promise<T>;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return response.text() as Promise<T>;
  }

  return response.json() as Promise<T>;
}

export async function downloadFile(path: string, filename: string): Promise<void> {
  const blob = await request<Blob>(path, { responseType: "blob" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
