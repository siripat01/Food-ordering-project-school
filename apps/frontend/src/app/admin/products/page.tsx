"use client";

import axios from "axios";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import Loading from "../../components/Loading";
import Navbar from "../../components/Navbar";
import ProductImage from "../../components/ProductImage";
import api from "../../libs/axios";
import { useUserStore } from "../../store/user";

type ProductStatus = "available" | "unavailable" | "discontinued";

type Addon = {
  id: string;
  name: string;
  price: number;
  available: boolean;
};

type Product = {
  id: string;
  name: string;
  price: number;
  status: ProductStatus;
  description: string | null;
  image_url: string | null;
  addons: Addon[];
};

type EditableAddon = {
  key: string;
  id: string;
  name: string;
  price: string;
  available: boolean;
};

type ProductFormState = {
  name: string;
  price: string;
  status: ProductStatus;
  description: string;
  imageUrl: string;
  addons: EditableAddon[];
};

const emptyForm: ProductFormState = {
  name: "",
  price: "",
  status: "available",
  description: "",
  imageUrl: "",
  addons: [],
};

const statusOptions: Array<{
  value: ProductStatus;
  label: string;
  classes: string;
}> = [
  {
    value: "available",
    label: "พร้อมขาย",
    classes: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  },
  {
    value: "unavailable",
    label: "ปิดขายชั่วคราว",
    classes: "bg-amber-50 text-amber-800 ring-amber-200",
  },
  {
    value: "discontinued",
    label: "ยกเลิกขาย",
    classes: "bg-slate-100 text-slate-700 ring-slate-200",
  },
];

function newAddon(addon?: Addon, index = 0): EditableAddon {
  return {
    key: `${addon?.id ?? "new"}-${index}-${Date.now()}`,
    id: addon?.id ?? "",
    name: addon?.name ?? "",
    price: addon ? String(addon.price) : "",
    available: addon?.available ?? true,
  };
}

function productToForm(product: Product): ProductFormState {
  return {
    name: product.name,
    price: String(product.price),
    status: product.status,
    description: product.description ?? "",
    imageUrl: product.image_url ?? "",
    addons: product.addons.map(newAddon),
  };
}

function statusMeta(status: ProductStatus) {
  return statusOptions.find((option) => option.value === status) ?? statusOptions[2];
}

function responseError(error: unknown, fallback: string) {
  if (!axios.isAxiosError(error)) return fallback;
  if (error.response?.status === 401) return "กรุณาเข้าสู่ระบบใหม่";
  if (error.response?.status === 403) return "บัญชีนี้ไม่มีสิทธิ์จัดการเมนู";
  const detail = error.response?.data?.detail;
  if (typeof detail === "string" && detail.length <= 180) return detail;
  return fallback;
}

function readProducts(value: unknown): Product[] | null {
  if (!value || typeof value !== "object" || !("products" in value)) return null;
  const products = (value as { products: unknown }).products;
  return Array.isArray(products) ? (products as Product[]) : null;
}

export default function AdminProductsPage() {
  const role = useUserStore((state) => state.role);
  const canManageProducts = role === "admin";
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<ProductStatus | "all">("all");
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [form, setForm] = useState<ProductFormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [confirmDiscontinueId, setConfirmDiscontinueId] = useState<string | null>(null);
  const [discontinuingId, setDiscontinuingId] = useState<string | null>(null);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await api.get("/admin/products");
      const loaded = readProducts(response.data);
      if (!loaded) throw new Error("Invalid products response");
      setProducts(loaded);
    } catch (error) {
      setLoadError(responseError(error, "โหลดรายการเมนูไม่สำเร็จ กรุณาลองใหม่อีกครั้ง"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (canManageProducts) void loadProducts();
    else setLoading(false);
  }, [canManageProducts, loadProducts]);

  const visibleProducts = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase("th");
    return products.filter((product) => {
      const matchesStatus = statusFilter === "all" || product.status === statusFilter;
      const matchesSearch =
        !normalizedSearch ||
        product.name.toLocaleLowerCase("th").includes(normalizedSearch) ||
        product.description?.toLocaleLowerCase("th").includes(normalizedSearch) ||
        product.addons.some((addon) =>
          addon.name.toLocaleLowerCase("th").includes(normalizedSearch),
        );
      return matchesStatus && Boolean(matchesSearch);
    });
  }, [products, search, statusFilter]);

  const counts = useMemo(
    () => ({
      all: products.length,
      available: products.filter((product) => product.status === "available").length,
      unavailable: products.filter((product) => product.status === "unavailable").length,
      discontinued: products.filter((product) => product.status === "discontinued").length,
    }),
    [products],
  );

  const resetForm = () => {
    setEditingProduct(null);
    setForm(emptyForm);
    setMutationError("");
  };

  const startEditing = (product: Product) => {
    setEditingProduct(product);
    setForm(productToForm(product));
    setMutationError("");
    setSuccessMessage("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const updateAddon = (key: string, changes: Partial<EditableAddon>) => {
    setForm((current) => ({
      ...current,
      addons: current.addons.map((addon) =>
        addon.key === key ? { ...addon, ...changes } : addon,
      ),
    }));
  };

  const removeAddon = (key: string) => {
    setForm((current) => ({
      ...current,
      addons: current.addons.filter((addon) => addon.key !== key),
    }));
  };

  const validateForm = () => {
    if (!form.name.trim()) return "กรุณาใส่ชื่อเมนู";
    const price = Number(form.price);
    if (!Number.isFinite(price) || price < 0) return "ราคาต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป";
    const addonIds = new Set<string>();
    for (const addon of form.addons) {
      const id = addon.id.trim();
      if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
        return "รหัสตัวเลือกเสริมใช้ได้เฉพาะ a-z, A-Z, 0-9, _ และ -";
      }
      if (addonIds.has(id)) return "รหัสตัวเลือกเสริมต้องไม่ซ้ำกัน";
      addonIds.add(id);
      if (!addon.name.trim()) return "กรุณาใส่ชื่อตัวเลือกเสริมให้ครบ";
      const addonPrice = Number(addon.price);
      if (!Number.isFinite(addonPrice) || addonPrice < 0) {
        return "ราคาตัวเลือกเสริมต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป";
      }
    }
    return "";
  };

  const submitProduct = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setMutationError("");
    setSuccessMessage("");
    const validationError = validateForm();
    if (validationError) {
      setMutationError(validationError);
      return;
    }

    const payload = {
      name: form.name.trim(),
      price: Number(form.price),
      status: form.status,
      description: form.description.trim() || null,
      image_url: form.imageUrl.trim() || null,
      addons: form.addons.map((addon) => ({
        id: addon.id.trim(),
        name: addon.name.trim(),
        price: Number(addon.price),
        available: addon.available,
      })),
    };

    setSaving(true);
    try {
      if (editingProduct) {
        await api.patch(`/products/${editingProduct.id}`, payload);
        setSuccessMessage(`อัปเดต “${payload.name}” แล้ว`);
      } else {
        await api.post("/products", payload);
        setSuccessMessage(`เพิ่ม “${payload.name}” ลงในเมนูแล้ว`);
      }
      resetForm();
      await loadProducts();
    } catch (error) {
      setMutationError(responseError(error, "บันทึกเมนูไม่สำเร็จ กรุณาตรวจข้อมูลแล้วลองใหม่"));
    } finally {
      setSaving(false);
    }
  };

  const discontinueProduct = async (product: Product) => {
    setMutationError("");
    setSuccessMessage("");
    setDiscontinuingId(product.id);
    try {
      await api.delete(`/products/${product.id}`);
      setSuccessMessage(`ยกเลิกขาย “${product.name}” แล้ว ข้อมูลเดิมยังถูกเก็บไว้`);
      setConfirmDiscontinueId(null);
      if (editingProduct?.id === product.id) resetForm();
      await loadProducts();
    } catch (error) {
      setMutationError(responseError(error, "ยกเลิกขายเมนูไม่สำเร็จ กรุณาลองใหม่"));
    } finally {
      setDiscontinuingId(null);
    }
  };

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell py-10 sm:py-14">
        {!canManageProducts ? (
          <div className="surface-card mx-auto max-w-lg p-10 text-center">
            <span className="text-4xl" aria-hidden="true">🔒</span>
            <h1 className="mt-4 text-2xl font-black">ไม่สามารถจัดการเมนูได้</h1>
            <p className="mt-3 leading-7 text-[var(--muted)]">
              หน้านี้สำหรับบัญชี Admin เท่านั้น หากเพิ่งได้รับสิทธิ์ กรุณารีเฟรชหน้าอีกครั้ง
            </p>
          </div>
        ) : (
          <>
            <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="eyebrow">Catalog management</p>
                <h1 className="mt-2 text-4xl font-black tracking-tight">จัดการเมนูร้าน</h1>
                <p className="mt-3 max-w-2xl text-[var(--muted)]">
                  ราคาและตัวเลือกที่บันทึกที่นี่จะเป็นข้อมูลอ้างอิงที่ backend ใช้คำนวณออเดอร์
                </p>
              </div>
              <button type="button" className="secondary-button" onClick={resetForm}>
                + เพิ่มเมนูใหม่
              </button>
            </div>

            {(loadError || mutationError || successMessage) && (
              <div
                className={`mb-5 rounded-xl border p-4 text-sm ${
                  successMessage
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-rose-200 bg-rose-50 text-rose-800"
                }`}
                role={successMessage ? "status" : "alert"}
              >
                {successMessage || mutationError || loadError}
              </div>
            )}

            <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
              <div className="min-w-0">
                <div className="surface-card mb-5 p-4 sm:p-5">
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                    <label>
                      <span className="sr-only">ค้นหาเมนู</span>
                      <input
                        type="search"
                        className="field-control"
                        placeholder="ค้นหาชื่อเมนู คำอธิบาย หรือตัวเลือกเสริม…"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="secondary-button min-h-11 px-4 text-sm"
                      onClick={() => void loadProducts()}
                      disabled={loading}
                    >
                      รีเฟรช
                    </button>
                  </div>
                  <div className="mt-4 flex gap-2 overflow-x-auto pb-1" aria-label="กรองเมนูตามสถานะ">
                    {(
                      [
                        ["all", "ทั้งหมด", counts.all],
                        ["available", "พร้อมขาย", counts.available],
                        ["unavailable", "ปิดชั่วคราว", counts.unavailable],
                        ["discontinued", "ยกเลิกขาย", counts.discontinued],
                      ] as const
                    ).map(([value, label, count]) => (
                      <button
                        key={value}
                        type="button"
                        className={`whitespace-nowrap rounded-full px-4 py-2 text-sm font-bold ${
                          statusFilter === value
                            ? "bg-[var(--brand)] text-white"
                            : "border border-[var(--border)] bg-[var(--surface)]"
                        }`}
                        aria-pressed={statusFilter === value}
                        onClick={() => setStatusFilter(value)}
                      >
                        {label} <span className="opacity-70">{count}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {loading ? (
                  <Loading label="กำลังโหลดรายการเมนู…" fullPage={false} />
                ) : visibleProducts.length === 0 ? (
                  <div className="surface-card p-10 text-center">
                    <span className="text-4xl" aria-hidden="true">🍽️</span>
                    <h2 className="mt-4 text-xl font-black">ไม่พบเมนู</h2>
                    <p className="mt-2 text-[var(--muted)]">
                      ลองเปลี่ยนคำค้นหาหรือตัวกรอง หรือเพิ่มเมนูแรกของร้าน
                    </p>
                  </div>
                ) : (
                  <div className="grid gap-4 xl:grid-cols-2">
                    {visibleProducts.map((product) => {
                      const status = statusMeta(product.status);
                      return (
                        <article key={product.id} className="surface-card overflow-hidden">
                          <ProductImage
                            imageUrl={product.image_url}
                            name={product.name}
                            className="aspect-[16/8] w-full bg-[var(--surface-muted)]"
                          />
                          <div className="p-5">
                            <div className="flex items-start justify-between gap-4">
                              <div className="min-w-0">
                                <span
                                  className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${status.classes}`}
                                >
                                  {status.label}
                                </span>
                                <h2 className="mt-3 truncate text-xl font-black">{product.name}</h2>
                              </div>
                              <strong className="shrink-0 text-lg text-[var(--brand)]">
                                ฿{product.price.toFixed(2)}
                              </strong>
                            </div>
                            {product.description && (
                              <p className="mt-3 line-clamp-2 text-sm leading-6 text-[var(--muted)]">
                                {product.description}
                              </p>
                            )}
                            <p className="mt-3 text-xs text-[var(--muted)]">
                              {product.addons.length > 0
                                ? `${product.addons.length} ตัวเลือกเสริม · ${product.addons.filter((addon) => addon.available).length} พร้อมขาย`
                                : "ไม่มีตัวเลือกเสริม"}
                            </p>
                            <div className="mt-5 flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
                              <button
                                type="button"
                                className="primary-button min-h-10 px-4 text-sm"
                                onClick={() => startEditing(product)}
                              >
                                แก้ไข
                              </button>
                              {product.status !== "discontinued" &&
                                (confirmDiscontinueId === product.id ? (
                                  <>
                                    <button
                                      type="button"
                                      className="secondary-button min-h-10 border-rose-300 px-4 text-sm text-rose-700"
                                      disabled={discontinuingId === product.id}
                                      onClick={() => void discontinueProduct(product)}
                                    >
                                      {discontinuingId === product.id ? "กำลังยกเลิก…" : "ยืนยันยกเลิกขาย"}
                                    </button>
                                    <button
                                      type="button"
                                      className="min-h-10 px-3 text-sm font-bold text-[var(--muted)]"
                                      onClick={() => setConfirmDiscontinueId(null)}
                                    >
                                      ไม่ยกเลิก
                                    </button>
                                  </>
                                ) : (
                                  <button
                                    type="button"
                                    className="secondary-button min-h-10 px-4 text-sm text-rose-700"
                                    onClick={() => setConfirmDiscontinueId(product.id)}
                                  >
                                    ยกเลิกขาย
                                  </button>
                                ))}
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </div>

              <aside className="surface-card p-5 lg:sticky lg:top-24 sm:p-6">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="eyebrow">{editingProduct ? "Edit product" : "New product"}</p>
                    <h2 className="mt-2 text-2xl font-black">
                      {editingProduct ? "แก้ไขเมนู" : "เพิ่มเมนูใหม่"}
                    </h2>
                  </div>
                  {editingProduct && (
                    <button
                      type="button"
                      className="text-sm font-bold text-[var(--muted)] hover:text-[var(--brand)]"
                      onClick={resetForm}
                    >
                      ยกเลิก
                    </button>
                  )}
                </div>

                <form className="mt-6 space-y-4" onSubmit={submitProduct}>
                  <label className="block">
                    <span className="mb-1.5 block text-sm font-bold">ชื่อเมนู *</span>
                    <input
                      className="field-control"
                      maxLength={150}
                      required
                      value={form.name}
                      onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                    />
                  </label>

                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="mb-1.5 block text-sm font-bold">ราคา *</span>
                      <input
                        type="number"
                        className="field-control"
                        min="0"
                        step="0.01"
                        inputMode="decimal"
                        required
                        value={form.price}
                        onChange={(event) => setForm((current) => ({ ...current, price: event.target.value }))}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1.5 block text-sm font-bold">สถานะ</span>
                      <select
                        className="field-control"
                        value={form.status}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            status: event.target.value as ProductStatus,
                          }))
                        }
                      >
                        {statusOptions.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <label className="block">
                    <span className="mb-1.5 block text-sm font-bold">คำอธิบาย</span>
                    <textarea
                      className="field-control min-h-24 resize-y"
                      maxLength={1000}
                      value={form.description}
                      onChange={(event) =>
                        setForm((current) => ({ ...current, description: event.target.value }))
                      }
                    />
                  </label>

                  <label className="block">
                    <span className="mb-1.5 block text-sm font-bold">URL รูปภาพ</span>
                    <input
                      type="url"
                      className="field-control"
                      placeholder="https://…"
                      value={form.imageUrl}
                      onChange={(event) => setForm((current) => ({ ...current, imageUrl: event.target.value }))}
                    />
                  </label>

                  <fieldset className="rounded-2xl border border-[var(--border)] p-4">
                    <legend className="sr-only">ตัวเลือกเสริม</legend>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-black" aria-hidden="true">ตัวเลือกเสริม</span>
                      <button
                        type="button"
                        className="text-sm font-bold text-[var(--brand)]"
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            addons: [...current.addons, newAddon(undefined, current.addons.length)],
                          }))
                        }
                      >
                        + เพิ่มตัวเลือก
                      </button>
                    </div>
                    {form.addons.length === 0 ? (
                      <p className="mt-3 text-sm text-[var(--muted)]">ยังไม่มีตัวเลือกเสริม</p>
                    ) : (
                      <div className="mt-4 space-y-4">
                        {form.addons.map((addon, index) => (
                          <div key={addon.key} className="rounded-xl bg-[var(--surface-muted)] p-3">
                            <div className="mb-3 flex items-center justify-between">
                              <strong className="text-sm">ตัวเลือก {index + 1}</strong>
                              <button
                                type="button"
                                className="text-xs font-bold text-rose-700"
                                onClick={() => removeAddon(addon.key)}
                              >
                                ลบ
                              </button>
                            </div>
                            <div className="space-y-3">
                              <input
                                className="field-control bg-white"
                                placeholder="รหัส เช่น fried-egg"
                                maxLength={64}
                                required
                                value={addon.id}
                                onChange={(event) => updateAddon(addon.key, { id: event.target.value })}
                                aria-label={`รหัสตัวเลือก ${index + 1}`}
                              />
                              <input
                                className="field-control bg-white"
                                placeholder="ชื่อ เช่น ไข่ดาว"
                                maxLength={100}
                                required
                                value={addon.name}
                                onChange={(event) => updateAddon(addon.key, { name: event.target.value })}
                                aria-label={`ชื่อตัวเลือก ${index + 1}`}
                              />
                              <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
                                <input
                                  type="number"
                                  className="field-control bg-white"
                                  min="0"
                                  step="0.01"
                                  inputMode="decimal"
                                  placeholder="ราคาเพิ่ม"
                                  required
                                  value={addon.price}
                                  onChange={(event) => updateAddon(addon.key, { price: event.target.value })}
                                  aria-label={`ราคาตัวเลือก ${index + 1}`}
                                />
                                <label className="flex items-center gap-2 text-sm font-bold">
                                  <input
                                    type="checkbox"
                                    checked={addon.available}
                                    onChange={(event) => updateAddon(addon.key, { available: event.target.checked })}
                                  />
                                  พร้อมขาย
                                </label>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </fieldset>

                  <button type="submit" className="primary-button w-full" disabled={saving}>
                    {saving
                      ? "กำลังบันทึก…"
                      : editingProduct
                        ? "บันทึกการแก้ไข"
                        : "เพิ่มเมนู"}
                  </button>
                </form>
              </aside>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
