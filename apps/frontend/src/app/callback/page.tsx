"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import api from "../libs/axios";
import Loading from "../components/Loading";
import { useUserStore } from "../store/user";

export default function Callback() {
  const router = useRouter();
  const setUser = useUserStore((state) => state.setUser);

  useEffect(() => {
    const loadSession = async () => {
      try {
        const response = await api.get("/users/me");
        setUser(response.data);
      } finally {
        router.replace("/");
      }
    };
    void loadSession();
  }, [router, setUser]);

  return <Loading label="กำลังเข้าสู่ระบบอย่างปลอดภัย…" />;
}
