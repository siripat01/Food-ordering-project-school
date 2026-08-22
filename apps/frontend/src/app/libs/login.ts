import { apiBaseUrl } from "./axios";

export const login = () => {
  window.location.assign(`${apiBaseUrl}/auth/line?origin=web`);
};
