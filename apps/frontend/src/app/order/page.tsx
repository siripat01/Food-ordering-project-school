"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import Loading from "../components/Loading";
import Navbar from "../components/Navbar";
import StatusBadge, { getStatusLabel, type OrderStatus } from "../components/StatusBadge";
import api from "../libs/axios";
import { login } from "../libs/login";
import { useUserStore } from "../store/user";

type Order = {
  id: string;
  total: number;
  status: OrderStatus;
  created_at: string;
  items: Array<{ product_name_snapshot: string; quantity: number }>;
};

const statuses: OrderStatus[] = [
  "pending",
  "confirmed",
  "preparing",
  "ready",
  "completed",
  "cancelled",
];

export default function OrdersPage() {
  const userId = useUserStore((state) => state.id);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  const loadOrders = useCallback(() => {
    if (!userId) {
      setOrders([]);
      return;
    }
    setError(false);
    setOrders(null);
    const query = statusFilter ? `?order_status=${statusFilter}` : "";
    api
      .get(`/orders/me${query}`)
      .then((response) => setOrders(response.data.orders))
      .catch(() => {
        setError(true);
        setOrders([]);
      });
  }, [statusFilter, userId]);

  useEffect(() => loadOrders(), [loadOrders]);

  const cancelOrder = async (orderId: string) => {
    setCancellingId(orderId);
    setError(false);
    try {
      await api.post(`/orders/${orderId}/cancel`);
      await loadOrders();
    } catch {
      setError(true);
    } finally {
      setCancellingId(null);
    }
  };

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell py-12 sm:py-16">
        <div className="mb-9 flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="eyebrow">Order tracking</p>
            <h1 className="mt-2 text-4xl font-black tracking-tight">รายการสั่งซื้อของฉัน</h1>
            <p className="mt-3 text-[var(--muted)]">ติดตามความคืบหน้าตั้งแต่ร้านยืนยันจนถึงพร้อมรับ</p>
          </div>
          {userId && <Link href="/product" className="primary-button">สั่งอาหารเพิ่ม</Link>}
        </div>

        {!userId ? (
          <div className="surface-card mx-auto max-w-xl p-10 text-center">
            <span className="text-5xl" aria-hidden="true">🧾</span>
            <h2 className="mt-5 text-2xl font-black">เข้าสู่ระบบเพื่อติดตามออเดอร์</h2>
            <p className="mt-3 leading-7 text-[var(--muted)]">รายการทั้งหมดผูกกับบัญชี LINE ของคุณเพื่อป้องกันการเข้าถึงออเดอร์ของผู้อื่น</p>
            <button className="primary-button mt-6" onClick={login}>เข้าสู่ระบบด้วย LINE</button>
          </div>
        ) : (
          <>
            <label className="mb-7 block max-w-xs">
              <span className="mb-2 block text-sm font-bold">กรองตามสถานะ</span>
              <select className="field-control" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">ทุกสถานะ</option>
                {statuses.map((status) => <option key={status} value={status}>{getStatusLabel(status)}</option>)}
              </select>
            </label>

            {orders === null ? (
              <Loading label="กำลังโหลดรายการสั่งซื้อ…" fullPage={false} />
            ) : error ? (
              <div className="surface-card p-8 text-center">
                <h2 className="text-xl font-bold">ดำเนินการไม่สำเร็จ</h2>
                <p className="mt-2 text-[var(--muted)]">ข้อมูลอาจมีการเปลี่ยนแปลง กรุณาลองโหลดใหม่</p>
                <button type="button" className="secondary-button mt-5" onClick={loadOrders}>โหลดอีกครั้ง</button>
              </div>
            ) : orders.length === 0 ? (
              <div className="surface-card p-10 text-center">
                <span className="text-4xl" aria-hidden="true">🍲</span>
                <h2 className="mt-4 text-xl font-black">{statusFilter ? "ไม่มีออเดอร์ในสถานะนี้" : "ยังไม่มีรายการสั่งซื้อ"}</h2>
                <p className="mt-2 text-[var(--muted)]">{statusFilter ? "ลองเลือกสถานะอื่นเพื่อดูรายการก่อนหน้า" : "เลือกเมนูโปรด แล้วออเดอร์จะปรากฏที่นี่"}</p>
              </div>
            ) : (
              <div className="space-y-4">
                {orders.map((order) => (
                  <article key={order.id} className="surface-card p-5 sm:p-6">
                    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                      <div>
                        <div className="flex flex-wrap items-center gap-3">
                          <StatusBadge status={order.status} />
                          <time className="text-xs text-[var(--muted)]" dateTime={order.created_at}>
                            {new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(new Date(order.created_at))}
                          </time>
                        </div>
                        <h2 className="mt-4 font-black leading-7">
                          {order.items.map((item) => `${item.quantity}× ${item.product_name_snapshot}`).join(", ")}
                        </h2>
                        <p className="mt-2 text-xs text-[var(--muted)]">Order #{order.id.slice(-8).toUpperCase()}</p>
                      </div>
                      <p className="text-xl font-black text-[var(--brand)]">฿{order.total.toFixed(2)}</p>
                    </div>
                    {(order.status === "pending" || order.status === "confirmed") && (
                      <div className="mt-5 border-t border-[var(--border)] pt-4">
                        <button
                          type="button"
                          className="text-sm font-bold text-rose-700 hover:underline disabled:opacity-50"
                          disabled={cancellingId === order.id}
                          onClick={() => void cancelOrder(order.id)}
                        >
                          {cancellingId === order.id ? "กำลังยกเลิก…" : "ยกเลิกออเดอร์นี้"}
                        </button>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}
