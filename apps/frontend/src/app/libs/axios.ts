import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

export const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const api = axios.create({
  baseURL: apiBaseUrl,
  withCredentials: true,
  timeout: 10_000,
  headers: { "Content-Type": "application/json" },
});

type RetriableRequest = InternalAxiosRequestConfig & { _retriedAfterRefresh?: boolean };

let refreshInFlight: Promise<void> | undefined;

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const request = error.config as RetriableRequest | undefined;
    if (
      error.response?.status !== 401 ||
      !request ||
      request._retriedAfterRefresh ||
      request.url?.includes("/auth/refresh")
    ) {
      return Promise.reject(error);
    }
    request._retriedAfterRefresh = true;
    refreshInFlight ??= api.post("/auth/refresh").then(() => undefined).finally(() => {
      refreshInFlight = undefined;
    });
    try {
      await refreshInFlight;
      return api(request);
    } catch {
      return Promise.reject(error);
    }
  },
);

export default api;
