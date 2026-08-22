import { create } from "zustand";
import { persist } from "zustand/middleware";

export type UserRole = "customer" | "staff" | "admin";

type UserState = {
  id: string;
  role: UserRole;
  display_name: string;
  picture_url: string | null;
  email: string | null;
  active: boolean;
};

type UserActions = {
  setUser: (user: Partial<UserState>) => void;
  clearUser: () => void;
};

const emptyUser: UserState = {
  id: "",
  role: "customer",
  display_name: "",
  picture_url: null,
  email: null,
  active: false,
};

export const useUserStore = create<UserState & UserActions>()(
  persist(
    (set) => ({
      ...emptyUser,
      setUser: (user) => set(user),
      clearUser: () => set(emptyUser),
    }),
    { name: "user-session" },
  ),
);
