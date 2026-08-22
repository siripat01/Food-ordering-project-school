"use client";

import { useCallback, useEffect, useState } from "react";

import Loading from "../components/Loading";
import Navbar from "../components/Navbar";
import StatusBadge, { getStatusLabel, type OrderStatus } from "../components/StatusBadge";
import api from "../libs/axios";
import { useUserStore } from "../store/user";

type Order = {
  id: string;
  user_id: string;
  total: number;
  status: OrderStatus;
  created_at: string;
  items: Array<{ product_name_snapshot: string; quantity: number }>;
};

const nextStatuses: Record<OrderStatus, OrderStatus[]> = {
  pending: ["confirmed", "cancelled"],
  confirmed: ["preparing", "cancelled"],
  preparing: ["ready", "cancelled"],
  ready: ["completed"],
  completed: [],
  cancelled: [],
};

export default function StaffQueuePage() {
  const role = useUserStore((state) => state.role);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    setError(false);
    try {
      const response = await api.get("/staff/orders");
      setOrders(response.data.orders);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (role === "staff" || role === "admin") void loadQueue();
    else setLoading(false);
  }, [loadQueue, role]);

  const transition = async (orderId: string, status: OrderStatus) => {
    setUpdatingId(orderId);
    setError(false);
    try {
      await api.patch(`/staff/orders/${orderId}/status`, { status });
      await loadQueue();
    } catch {
      setError(true);
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell py-12 sm:py-16">
        {role !== "staff" && role !== "admin" ? (
          <div className="surface-card mx-auto max-w-lg p-10 text-center">
            <span className="text-4xl" aria-hidden="true">🔒</span>
            <h1 className="mt-4 text-2xl font-black">ไม่สามารถเข้าถึงคิวร้านได้</h1>
            <p className="mt-3 leading-7 text-[var(--muted)]">หน้านี้สำหรับบัญชี Staff และ Admin เท่านั้น หากคิดว่าเป็นข้อผิดพลาด กรุณาติดต่อผู้ดูแลระบบ</p>
          </div>
        ) : (
          <>
            <div className="mb-9 flex flex-wrap items-end justify-between gap-4">
              <div><p className="eyebrow">Operations</p><h1 className="mt-2 text-4xl font-black tracking-tight">คิวออเดอร์ร้าน</h1><p className="mt-3 text-[var(--muted)]">อัปเดตสถานะตามลำดับการทำงานที่ระบบอนุญาต</p></div>
              <button type="button" className="secondary-button" onClick={() => void loadQueue()} disabled={loading}>รีเฟรชคิว</button>
            </div>

            {error && (
              <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800" role="alert">
                อัปเดตคิวไม่สำเร็จ ข้อมูลด้านล่างอาจไม่ใช่ข้อมูลล่าสุด กรุณาลองรีเฟรชอีกครั้ง
              </div>
            )}

            {loading ? (
              <Loading label="กำลังโหลดคิวออเดอร์…" fullPage={false} />
            ) : orders.length === 0 ? (
              <div className="surface-card p-10 text-center"><span className="text-4xl" aria-hidden="true">✓</span><h2 className="mt-4 text-xl font-black">ยังไม่มีออเดอร์ในคิว</h2><p className="mt-2 text-[var(--muted)]">เมื่อมีออเดอร์ใหม่ รายการจะปรากฏที่นี่</p></div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {orders.map((order) => (
                  <article key={order.id} className="surface-card p-5 sm:p-6">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <StatusBadge status={order.status} />
                        <h2 className="mt-4 font-black leading-7">{order.items.map((item) => `${item.quantity}× ${item.product_name_snapshot}`).join(", ")}</h2>
                        <p className="mt-2 text-xs text-[var(--muted)]">Order #{order.id.slice(-8).toUpperCase()}</p>
                      </div>
                      <p className="font-black text-[var(--brand)]">฿{order.total.toFixed(2)}</p>
                    </div>
                    <div className="mt-5 flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
                      {nextStatuses[order.status].length === 0 ? (
                        <span className="text-sm text-[var(--muted)]">ปิดงานแล้ว — ไม่มีสถานะถัดไป</span>
                      ) : nextStatuses[order.status].map((status) => (
                        <button
                          key={status}
                          type="button"
                          className={status === "cancelled" ? "secondary-button min-h-10 px-4 text-sm text-rose-700" : "primary-button min-h-10 px-4 text-sm"}
                          disabled={updatingId === order.id}
                          onClick={() => void transition(order.id, status)}
                        >
                          {updatingId === order.id ? "กำลังอัปเดต…" : `เปลี่ยนเป็น ${getStatusLabel(status)}`}
                        </button>
                      ))}
                    </div>
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
