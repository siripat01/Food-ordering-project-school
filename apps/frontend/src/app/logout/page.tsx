"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import api from "../libs/axios";
import Loading from "../components/Loading";
import { useUserStore } from "../store/user";

export default function Logout() {
  const router = useRouter();
  const clearUser = useUserStore((state) => state.clearUser);

  useEffect(() => {
    const logout = async () => {
      try {
        await api.post("/auth/logout");
      } finally {
        clearUser();
        router.replace("/");
      }
    };
    void logout();
  }, [clearUser, router]);

  return <Loading label="กำลังออกจากระบบ…" />;
}
