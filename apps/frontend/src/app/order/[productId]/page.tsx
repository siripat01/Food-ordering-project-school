"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Loading from "@/app/components/Loading";
import Navbar from "@/app/components/Navbar";
import ProductImage from "@/app/components/ProductImage";
import api from "@/app/libs/axios";
import { login } from "@/app/libs/login";
import { useUserStore } from "@/app/store/user";

type Addon = { id: string; name: string; price: number; available: boolean };
type Product = {
  id: string;
  name: string;
  price: number;
  status: "available" | "unavailable" | "discontinued";
  image_url: string | null;
  description: string | null;
  addons: Addon[];
};

export default function OrderProductPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const { productId } = use(params);
  const router = useRouter();
  const userId = useUserStore((state) => state.id);
  const [product, setProduct] = useState<Product | null>(null);
  const [note, setNote] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [addonIds, setAddonIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [submitError, setSubmitError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const idempotencyKey = useRef<string | null>(null);

  const loadProduct = useCallback(() => {
    setLoading(true);
    setLoadError(false);
    api
      .get(`/products/${productId}`)
      .then((response) => setProduct(response.data))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false));
  }, [productId]);

  useEffect(() => loadProduct(), [loadProduct]);

  const submit = async () => {
    if (!userId) {
      login();
      return;
    }
    if (!product || product.status !== "available" || submitting) return;

    setSubmitting(true);
    setSubmitError(false);
    idempotencyKey.current ??= crypto.randomUUID();
    try {
      await api.post(
        "/orders",
        { items: [{ product_id: product.id, quantity, addon_ids: addonIds, note }] },
        { headers: { "Idempotency-Key": idempotencyKey.current } },
      );
      router.push("/order");
    } catch {
      setSubmitError(true);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <><Navbar /><Loading label="กำลังโหลดรายละเอียดเมนู…" /></>;

  if (loadError || !product) {
    return (
      <main className="min-h-screen">
        <Navbar />
        <section className="page-shell grid min-h-[65vh] place-items-center py-16">
          <div className="surface-card max-w-lg p-10 text-center">
            <span className="text-4xl" aria-hidden="true">🍽️</span>
            <h1 className="mt-4 text-2xl font-black">ไม่พบรายละเอียดเมนู</h1>
            <p className="mt-2 text-[var(--muted)]">เมนูอาจถูกปรับปรุงชั่วคราว กรุณาลองใหม่หรือกลับไปเลือกเมนูอื่น</p>
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <button type="button" className="secondary-button" onClick={loadProduct}>ลองอีกครั้ง</button>
              <Link href="/product" className="primary-button">กลับหน้าเมนู</Link>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const availableAddons = product.addons.filter((addon) => addon.available);
  const displayEstimate =
    (product.price +
      availableAddons
        .filter((addon) => addonIds.includes(addon.id))
        .reduce((sum, addon) => sum + addon.price, 0)) * quantity;

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell py-10 sm:py-16">
        <Link href="/product" className="inline-flex items-center gap-2 text-sm font-bold text-[var(--brand)] hover:underline"><span aria-hidden="true">←</span> กลับไปเลือกเมนู</Link>
        <div className="mt-6 grid gap-8 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
          <div className="surface-card overflow-hidden lg:sticky lg:top-28">
            <ProductImage imageUrl={product.image_url} name={product.name} className="aspect-[4/3] w-full" />
            <div className="p-6">
              <p className="eyebrow">Menu detail</p>
              <div className="mt-2 flex items-start justify-between gap-4">
                <h1 className="text-3xl font-black tracking-tight">{product.name}</h1>
                <p className="shrink-0 text-xl font-black text-[var(--brand)]">฿{product.price.toFixed(2)}</p>
              </div>
              <p className="mt-4 leading-7 text-[var(--muted)]">{product.description || "เมนูอร่อยที่ร้านตั้งใจเตรียมให้คุณ"}</p>
            </div>
          </div>

          <div className="surface-card p-5 sm:p-7">
            <div className="flex items-center justify-between gap-4 border-b border-[var(--border)] pb-5">
              <div><p className="eyebrow">Customize</p><h2 className="mt-1 text-2xl font-black">ปรับรายการของคุณ</h2></div>
              <div className="flex items-center rounded-full border border-[var(--border)] bg-white p-1" aria-label="จำนวน">
                <button type="button" className="grid h-9 w-9 place-items-center rounded-full hover:bg-[#eef2eb] disabled:opacity-40" onClick={() => setQuantity((value) => Math.max(1, value - 1))} disabled={quantity <= 1} aria-label="ลดจำนวน">−</button>
                <output className="min-w-9 text-center font-black" aria-live="polite">{quantity}</output>
                <button type="button" className="grid h-9 w-9 place-items-center rounded-full hover:bg-[#eef2eb] disabled:opacity-40" onClick={() => setQuantity((value) => Math.min(20, value + 1))} disabled={quantity >= 20} aria-label="เพิ่มจำนวน">+</button>
              </div>
            </div>

            {availableAddons.length > 0 && (
              <fieldset className="mt-6">
                <legend className="font-black">ตัวเลือกเพิ่มเติม <span className="text-sm font-normal text-[var(--muted)]">(เลือกได้มากกว่า 1)</span></legend>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {availableAddons.map((addon) => (
                    <label key={addon.id} className={`flex items-center justify-between gap-3 rounded-xl border p-4 transition-colors ${addonIds.includes(addon.id) ? "border-[var(--brand)] bg-[#eef6f1]" : "border-[var(--border)] bg-white"}`}>
                      <span className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[var(--brand)]"
                          checked={addonIds.includes(addon.id)}
                          onChange={(event) => setAddonIds((current) => event.target.checked ? [...current, addon.id] : current.filter((id) => id !== addon.id))}
                        />
                        <span className="font-semibold">{addon.name}</span>
                      </span>
                      <span className="shrink-0 text-sm text-[var(--muted)]">+฿{addon.price.toFixed(2)}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            )}

            <label className="mt-6 block">
              <span className="font-black">หมายเหตุสำหรับร้าน <span className="font-normal text-[var(--muted)]">(ไม่บังคับ)</span></span>
              <textarea
                className="field-control mt-3 min-h-28 resize-y"
                maxLength={300}
                value={note}
                placeholder="เช่น ไม่ใส่ผัก หรือข้อควรระวังอื่น ๆ"
                onChange={(event) => setNote(event.target.value)}
              />
              <span className="mt-1 block text-right text-xs text-[var(--muted)]">{note.length}/300</span>
            </label>

            {submitError && (
              <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800" role="alert">
                ยังยืนยันรายการไม่ได้ กรุณาตรวจสอบข้อมูลแล้วลองอีกครั้ง ระบบจะไม่สร้างออเดอร์ซ้ำจากการลองเดิม
              </div>
            )}

            <div className="mt-6 rounded-2xl bg-[#eef2eb] p-5">
              <div className="flex items-center justify-between gap-4"><span className="font-bold">ยอดประมาณการ</span><strong className="text-2xl text-[var(--brand)]">฿{displayEstimate.toFixed(2)}</strong></div>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">ยอดสุดท้ายจะคำนวณและยืนยันอีกครั้งโดยระบบของร้านจาก Product ID และตัวเลือกที่เลือก</p>
            </div>

            {product.status !== "available" ? (
              <button type="button" className="secondary-button mt-5 w-full" disabled>เมนูนี้ยังไม่พร้อมจำหน่าย</button>
            ) : (
              <button className="primary-button mt-5 w-full" onClick={() => void submit()} disabled={submitting}>
                {submitting ? "กำลังยืนยันรายการ…" : userId ? "ยืนยันรายการ" : "เข้าสู่ระบบเพื่อสั่งอาหาร"}
              </button>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
