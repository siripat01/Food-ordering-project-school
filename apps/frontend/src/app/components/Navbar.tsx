"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import api from "../libs/axios";
import { useUserStore } from "../store/user";
import Login from "./Login";

export default function Navbar() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const { id, picture_url, role, setUser, clearUser } = useUserStore();

  useEffect(() => {
    const refreshSession = async () => {
      try {
        const response = await api.get("/users/me");
        setUser(response.data);
      } catch {
        clearUser();
      }
    };
    void refreshSession();
  }, [clearUser, setUser]);

  useEffect(() => setMenuOpen(false), [pathname]);

  const pages = [
    ["หน้าหลัก", "/"],
    ["เมนู", "/product"],
    ["รายการสั่งซื้อ", "/order"],
    ["เกี่ยวกับเรา", "/about"],
    ["ติดต่อ", "/contact"],
  ];
  if (role === "staff" || role === "admin") pages.push(["คิวร้าน", "/admin"]);
  if (role === "admin") pages.push(["จัดการเมนู", "/admin/products"]);

  const isActive = (path: string) =>
    path === "/" || path === "/admin" ? pathname === path : pathname.startsWith(path);

  const navigation = (
    <>
      {pages.map(([label, path]) => (
        <Link
          key={path}
          href={path}
          aria-current={isActive(path) ? "page" : undefined}
          className={`rounded-full px-3 py-2 text-sm font-semibold transition-colors ${
            isActive(path)
              ? "bg-[#e6efe9] text-[var(--brand)]"
              : "text-[#405047] hover:bg-[#f0f2ed] hover:text-[var(--brand)]"
          }`}
        >
          {label}
        </Link>
      ))}
    </>
  );

  return (
    <header className="sticky top-0 z-50 border-b border-[#dfe4dc] bg-[#fffdf8]/95 backdrop-blur-xl">
      <nav className="page-shell flex min-h-[4.5rem] items-center justify-between gap-4" aria-label="เมนูหลัก">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="หิวข้าว หน้าหลัก">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-[var(--brand)] text-lg text-white" aria-hidden="true">
            ช
          </span>
          <span className="leading-tight">
            <span className="block text-base font-black tracking-tight">หิวข้าว</span>
            <span className="block text-[0.65rem] font-bold tracking-[0.12em] text-[var(--muted)]">FOOD ORDERING</span>
          </span>
        </Link>

        <div className="hidden items-center gap-1 lg:flex">{navigation}</div>

        <div className="flex items-center justify-end gap-2">
          <div className="hidden sm:block">
            {id ? (
              <Link
                href="/logout"
                className="flex items-center gap-2 rounded-full border border-[var(--border)] bg-white px-2 py-1.5 text-sm font-semibold hover:border-[var(--brand)]"
                aria-label="ออกจากระบบ"
              >
                {picture_url ? (
                  // The URL is supplied by the verified LINE profile.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={picture_url} alt="" className="h-8 w-8 rounded-full object-cover" />
                ) : (
                  <span className="grid h-8 w-8 place-items-center rounded-full bg-[#e6efe9] text-[var(--brand)]" aria-hidden="true">
                    ฉัน
                  </span>
                )}
                <span className="hidden xl:inline">ออกจากระบบ</span>
              </Link>
            ) : (
              <Login className="min-h-10 px-4 text-sm" />
            )}
          </div>

          <button
            type="button"
            className="grid h-11 w-11 place-items-center rounded-full border border-[var(--border)] bg-white lg:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-navigation"
            aria-label={menuOpen ? "ปิดเมนู" : "เปิดเมนู"}
            onClick={() => setMenuOpen((current) => !current)}
          >
            <span className="text-xl" aria-hidden="true">{menuOpen ? "×" : "☰"}</span>
          </button>
        </div>
      </nav>

      <div
        id="mobile-navigation"
        className={`${menuOpen ? "block" : "hidden"} border-t border-[#e7e9e4] bg-[#fffdf8] px-4 pb-5 pt-3 lg:hidden`}
      >
        <div className="mx-auto flex max-w-md flex-col gap-1">{navigation}</div>
        <div className="mx-auto mt-3 max-w-md border-t border-[#e7e9e4] pt-4 sm:hidden">
          {id ? (
            <Link href="/logout" className="secondary-button w-full">ออกจากระบบ</Link>
          ) : (
            <Login className="w-full" />
          )}
        </div>
      </div>
    </header>
  );
}
