import axios from "axios";

export const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const api = axios.create({
  baseURL: apiBaseUrl,
  withCredentials: true,
  timeout: 10_000,
  headers: { "Content-Type": "application/json" },
});

export default api;
