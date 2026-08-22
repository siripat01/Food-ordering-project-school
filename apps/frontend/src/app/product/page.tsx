"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import Loading from "../components/Loading";
import Navbar from "../components/Navbar";
import ProductImage from "../components/ProductImage";
import api from "../libs/axios";

type Product = {
  id: string;
  name: string;
  status: "available" | "unavailable" | "discontinued";
  price: number;
  image_url: string | null;
  description: string | null;
};

export default function ProductPage() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState(false);

  const loadProducts = useCallback(() => {
    setProducts(null);
    setError(false);
    api
      .get("/products")
      .then((response) => setProducts(response.data.products))
      .catch(() => {
        setError(true);
        setProducts([]);
      });
  }, []);

  useEffect(() => loadProducts(), [loadProducts]);

  const filteredProducts = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase("th");
    if (!normalizedSearch) return products ?? [];
    return (products ?? []).filter((product) =>
      `${product.name} ${product.description ?? ""}`.toLocaleLowerCase("th").includes(normalizedSearch),
    );
  }, [products, search]);

  return (
    <main className="min-h-screen">
      <Navbar />
      {!products ? (
        <Loading label="กำลังโหลดเมนู…" />
      ) : (
        <section className="page-shell py-12 sm:py-16">
          <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
            <div>
              <p className="eyebrow">Our menu</p>
              <h1 className="mt-2 text-4xl font-black tracking-tight">เลือกมื้อที่ใช่สำหรับคุณ</h1>
              <p className="mt-3 text-[var(--muted)]">ราคาและตัวเลือกทั้งหมดอ้างอิงจากข้อมูลล่าสุดของร้าน</p>
            </div>
            <label className="w-full sm:max-w-sm">
              <span className="sr-only">ค้นหาเมนู</span>
              <input
                type="search"
                className="field-control"
                placeholder="ค้นหาชื่อหรือรายละเอียดเมนู…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
          </div>

          {error ? (
            <div className="surface-card mt-10 p-8 text-center">
              <p className="text-lg font-bold">โหลดเมนูไม่สำเร็จ</p>
              <p className="mt-2 text-sm text-[var(--muted)]">ตรวจสอบการเชื่อมต่อแล้วลองอีกครั้ง</p>
              <button type="button" className="secondary-button mt-5" onClick={loadProducts}>ลองอีกครั้ง</button>
            </div>
          ) : filteredProducts.length === 0 ? (
            <div className="surface-card mt-10 p-10 text-center">
              <span className="text-4xl" aria-hidden="true">🔎</span>
              <h2 className="mt-4 text-xl font-bold">ไม่พบเมนูที่ค้นหา</h2>
              <p className="mt-2 text-[var(--muted)]">ลองใช้คำค้นสั้นลงหรือค้นหาชื่อเมนูอื่น</p>
              {search && <button type="button" className="secondary-button mt-5" onClick={() => setSearch("")}>ล้างคำค้น</button>}
            </div>
          ) : (
            <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {filteredProducts.map((product) => (
                <article className="surface-card group flex min-h-[25rem] flex-col overflow-hidden" key={product.id}>
                  <ProductImage imageUrl={product.image_url} name={product.name} className="h-52 w-full transition-transform duration-300 group-hover:scale-[1.025]" />
                  <div className="flex flex-1 flex-col p-5">
                    <div className="flex items-start justify-between gap-4">
                      <h2 className="text-xl font-black">{product.name}</h2>
                      <p className="shrink-0 font-black text-[var(--brand)]">฿{product.price.toFixed(2)}</p>
                    </div>
                    <p className="mt-3 line-clamp-3 text-sm leading-6 text-[var(--muted)]">{product.description || "เมนูอร่อยที่ร้านตั้งใจเตรียมให้คุณ"}</p>
                    <div className="mt-auto pt-5">
                      {product.status === "available" ? (
                        <Link href={`/order/${product.id}`} className="primary-button w-full">เลือกรายการนี้ <span aria-hidden="true">→</span></Link>
                      ) : (
                        <button type="button" className="secondary-button w-full" disabled>ยังไม่พร้อมจำหน่าย</button>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
