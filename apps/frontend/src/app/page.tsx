"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import Loading from "./components/Loading";
import Navbar from "./components/Navbar";
import RecommendProduct from "./components/RecommendProduct";
import api from "./libs/axios";
import { useUserStore } from "./store/user";

type Product = { id: string; name: string; price: number; image_url: string | null };

export default function Home() {
  const { id, display_name } = useUserStore();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    api
      .get("/products")
      .then((response) => setProducts(response.data.products.slice(0, 3)))
      .catch(() => setLoadFailed(true))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell grid min-h-[34rem] items-center gap-10 py-14 lg:grid-cols-[1.12fr_0.88fr] lg:py-20">
        <div className="max-w-2xl">
          <p className="eyebrow mb-4">Fresh · Simple · Secure</p>
          <h1 className="text-4xl font-black leading-[1.12] tracking-[-0.035em] sm:text-5xl lg:text-6xl">
            {id ? `วันนี้ทานอะไรดี ${display_name || "คุณลูกค้า"}?` : "มื้ออร่อยที่สั่งง่าย และรู้สถานะทุกขั้นตอน"}
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-[var(--muted)]">
            เลือกเมนูและตัวเลือกที่ชอบ ระบบจะคำนวณราคาจากข้อมูลของร้าน แล้วติดตามออเดอร์ได้ในหน้าเดียว
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/product" className="primary-button px-7">ดูเมนูทั้งหมด <span aria-hidden="true">→</span></Link>
            <Link href="/order" className="secondary-button">ติดตามออเดอร์</Link>
          </div>
          <div className="mt-9 flex flex-wrap gap-x-7 gap-y-3 text-sm font-semibold text-[#53645a]">
            <span>✓ ราคายืนยันโดยร้าน</span>
            <span>✓ ติดตามสถานะได้</span>
            <span>✓ เข้าสู่ระบบด้วย LINE</span>
          </div>
        </div>

        <div className="surface-card relative overflow-hidden p-5 sm:p-7" aria-label="ขั้นตอนการสั่งอาหาร">
          <div className="absolute -right-16 -top-16 h-48 w-48 rounded-full bg-[#f4d6c4] opacity-70" aria-hidden="true" />
          <p className="relative text-sm font-bold text-[var(--brand)]">สามขั้นตอนง่าย ๆ</p>
          <ol className="relative mt-6 space-y-4">
            {[
              ["1", "เลือกเมนู", "ดูรายละเอียด ราคา และตัวเลือกเพิ่มเติม"],
              ["2", "ตรวจสอบรายการ", "ระบุจำนวนและหมายเหตุก่อนยืนยัน"],
              ["3", "ติดตามสถานะ", "รู้ทันทีเมื่อร้านรับออเดอร์และอาหารพร้อม"],
            ].map(([number, title, detail]) => (
              <li key={number} className="flex gap-4 rounded-2xl bg-white/80 p-4">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--brand)] font-black text-white">{number}</span>
                <span><strong className="block">{title}</strong><span className="mt-1 block text-sm leading-6 text-[var(--muted)]">{detail}</span></span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="border-y border-[#dde3dc] bg-[#eef2eb]/65">
        <div className="page-shell py-16 sm:py-20">
          <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
            <div><p className="eyebrow">Popular choices</p><h2 className="mt-2 text-3xl font-black tracking-tight">เมนูน่าลองวันนี้</h2></div>
            <Link href="/product" className="text-sm font-bold text-[var(--brand)] hover:underline">ดูเมนูทั้งหมด →</Link>
          </div>

          {loading ? (
            <Loading label="กำลังเลือกเมนูน่าลอง…" fullPage={false} />
          ) : loadFailed ? (
            <div className="surface-card p-8 text-center text-[var(--muted)]">ยังโหลดเมนูแนะนำไม่ได้ในขณะนี้ แต่คุณสามารถเปิดหน้าเมนูเพื่อลองอีกครั้งได้</div>
          ) : products.length === 0 ? (
            <div className="surface-card p-8 text-center text-[var(--muted)]">ร้านกำลังเตรียมเมนูใหม่ กลับมาดูอีกครั้งเร็ว ๆ นี้</div>
          ) : (
            <RecommendProduct fallbackProducts={products} />
          )}
        </div>
      </section>
    </main>
  );
}
