"use client";

import { useCallback, useEffect, useState } from "react";

import Loading from "../components/Loading";
import Navbar from "../components/Navbar";
import StatusBadge, { getStatusLabel, type OrderStatus } from "../components/StatusBadge";
import {
  type StreamConnectionState,
  useStaffOrderStream,
} from "../hooks/useStaffOrderStream";
import api from "../libs/axios";
import { useUserStore } from "../store/user";

type Order = {
  id: string;
  user_id: string;
  total: number;
  status: OrderStatus;
  created_at: string;
  updated_at?: string;
  items: Array<{
    product_name_snapshot: string;
    quantity: number;
    note?: string | null;
  }>;
};

const statuses: OrderStatus[] = [
  "pending",
  "confirmed",
  "preparing",
  "ready",
  "completed",
  "cancelled",
];

const activeStatuses = new Set<OrderStatus>([
  "pending",
  "confirmed",
  "preparing",
  "ready",
]);

const nextStatuses: Record<OrderStatus, OrderStatus[]> = {
  pending: ["confirmed", "cancelled"],
  confirmed: ["preparing", "cancelled"],
  preparing: ["ready", "cancelled"],
  ready: ["completed"],
  completed: [],
  cancelled: [],
};

const connectionLabels: Record<
  StreamConnectionState,
  { label: string; classes: string }
> = {
  connecting: {
    label: "กำลังเชื่อมต่อ",
    classes: "bg-amber-50 text-amber-800 ring-amber-200",
  },
  live: {
    label: "อัปเดตสด",
    classes: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  },
  reconnecting: {
    label: "กำลังเชื่อมต่อใหม่",
    classes: "bg-rose-50 text-rose-800 ring-rose-200",
  },
  disconnected: {
    label: "ยังไม่เชื่อมต่อ",
    classes: "bg-slate-100 text-slate-700 ring-slate-200",
  },
};

function isOrderItem(
  value: unknown,
): value is Order["items"][number] {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Order["items"][number]>;
  return (
    typeof item.product_name_snapshot === "string" &&
    typeof item.quantity === "number" &&
    Number.isInteger(item.quantity) &&
    item.quantity > 0 &&
    (item.note === undefined || item.note === null || typeof item.note === "string")
  );
}

function isOrder(value: unknown): value is Order {
  if (!value || typeof value !== "object") return false;
  const order = value as Partial<Order>;
  return (
    typeof order.id === "string" &&
    typeof order.user_id === "string" &&
    typeof order.total === "number" &&
    Number.isFinite(order.total) &&
    typeof order.status === "string" &&
    statuses.includes(order.status as OrderStatus) &&
    typeof order.created_at === "string" &&
    !Number.isNaN(Date.parse(order.created_at)) &&
    Array.isArray(order.items) &&
    order.items.every(isOrderItem)
  );
}

function readSnapshot(payload: unknown): Order[] | null {
  const candidate = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object" && "orders" in payload
      ? (payload as { orders: unknown }).orders
      : null;
  return Array.isArray(candidate) && candidate.every(isOrder) ? candidate : null;
}

function readUpdatedOrder(payload: unknown): Order | null {
  const candidate =
    payload && typeof payload === "object" && "order" in payload
      ? (payload as { order: unknown }).order
      : payload;
  return isOrder(candidate) ? candidate : null;
}

function sortQueue(orders: Order[]) {
  return [...orders].sort(
    (left, right) =>
      new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
}

function formatOrderTime(value: string) {
  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function StaffQueuePage() {
  const role = useUserStore((state) => state.role);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [mutationError, setMutationError] = useState(false);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<OrderStatus | "active">(
    "active",
  );

  const canManageQueue = role === "staff" || role === "admin";

  const loadQueue = useCallback(async () => {
    setLoadError(false);
    try {
      const response = await api.get("/staff/orders", {
        params: { include_terminal: true },
      });
      const queue = readSnapshot(response.data);
      if (!queue) throw new Error("Invalid order queue response");
      setOrders(sortQueue(queue));
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const applySnapshot = useCallback((payload: unknown) => {
    const queue = readSnapshot(payload);
    if (queue) {
      setOrders(sortQueue(queue));
      setLoadError(false);
      setLoading(false);
    }
  }, []);

  const applyOrderUpdate = useCallback((payload: unknown) => {
    const updatedOrder = readUpdatedOrder(payload);
    if (!updatedOrder) return;

    setOrders((current) =>
      sortQueue([
        ...current.filter((order) => order.id !== updatedOrder.id),
        updatedOrder,
      ]),
    );
  }, []);

  const { connectionState, lastEventAt, reconnect } = useStaffOrderStream({
    enabled: canManageQueue,
    onSnapshot: applySnapshot,
    onOrderUpdated: applyOrderUpdate,
  });

  useEffect(() => {
    if (canManageQueue) void loadQueue();
    else setLoading(false);
  }, [canManageQueue, loadQueue]);

  const transition = async (orderId: string, status: OrderStatus) => {
    setUpdatingId(orderId);
    setMutationError(false);
    try {
      const response = await api.patch(`/staff/orders/${orderId}/status`, { status });
      const updatedOrder = readUpdatedOrder(response.data);
      if (!updatedOrder) throw new Error("Invalid order update response");

      // Apply the committed PATCH response immediately. A follow-up queue read
      // or SSE reconnect may fail transiently through the proxy, but that must
      // not leave the UI showing the old status or report the mutation as failed.
      applyOrderUpdate(updatedOrder);
      setLoadError(false);
      void loadQueue();
    } catch {
      setMutationError(true);
    } finally {
      setUpdatingId(null);
    }
  };

  const visibleOrders = orders.filter((order) =>
    statusFilter === "active"
      ? activeStatuses.has(order.status)
      : order.status === statusFilter,
  );

  const queueCounts = {
    waiting: orders.filter((order) => order.status === "pending").length,
    preparing: orders.filter(
      (order) => order.status === "confirmed" || order.status === "preparing",
    ).length,
    ready: orders.filter((order) => order.status === "ready").length,
  };

  const connection = connectionLabels[connectionState];

  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell py-12 sm:py-16">
        {!canManageQueue ? (
          <div className="surface-card mx-auto max-w-lg p-10 text-center">
            <span className="text-4xl" aria-hidden="true">🔒</span>
            <h1 className="mt-4 text-2xl font-black">ไม่สามารถเข้าถึงคิวร้านได้</h1>
            <p className="mt-3 leading-7 text-[var(--muted)]">หน้านี้สำหรับบัญชี Staff และ Admin เท่านั้น หากคิดว่าเป็นข้อผิดพลาด กรุณาติดต่อผู้ดูแลระบบ</p>
          </div>
        ) : (
          <>
            <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="eyebrow">Live operations</p>
                <h1 className="mt-2 text-4xl font-black tracking-tight">คิวออเดอร์ร้าน</h1>
                <p className="mt-3 text-[var(--muted)]">เห็นออเดอร์ใหม่ทันที และอัปเดตสถานะตามลำดับงานที่ระบบอนุญาต</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`inline-flex min-h-10 items-center gap-2 rounded-full px-3 text-xs font-bold ring-1 ring-inset ${connection.classes}`}
                  aria-live="polite"
                >
                  <span className="relative flex h-2 w-2" aria-hidden="true">
                    {connectionState === "live" && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
                    <span className={`relative inline-flex h-2 w-2 rounded-full ${connectionState === "live" ? "bg-emerald-600" : "bg-current"}`} />
                  </span>
                  {connection.label}
                </span>
                {connectionState === "reconnecting" && (
                  <button type="button" className="secondary-button min-h-10 px-4 text-sm" onClick={reconnect}>เชื่อมต่อใหม่</button>
                )}
                <button type="button" className="secondary-button min-h-10 px-4 text-sm" onClick={() => void loadQueue()} disabled={loading}>รีเฟรชคิว</button>
              </div>
            </div>

            <div className="mb-7 grid gap-3 sm:grid-cols-3">
              {[
                ["รอรับออเดอร์", queueCounts.waiting, "text-amber-700"],
                ["กำลังดำเนินการ", queueCounts.preparing, "text-orange-700"],
                ["พร้อมส่งมอบ", queueCounts.ready, "text-emerald-700"],
              ].map(([label, value, color]) => (
                <div key={label} className="surface-card flex items-center justify-between px-5 py-4">
                  <span className="text-sm font-bold text-[var(--muted)]">{label}</span>
                  <strong className={`text-2xl ${color}`}>{value}</strong>
                </div>
              ))}
            </div>

            {(loadError || mutationError) && (
              <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800" role="alert">
                {mutationError
                  ? "เปลี่ยนสถานะไม่สำเร็จ ออเดอร์อาจถูกอัปเดตจากอุปกรณ์อื่น กรุณารีเฟรชแล้วลองอีกครั้ง"
                  : "โหลดคิวไม่สำเร็จ ข้อมูลด้านล่างอาจไม่ใช่ข้อมูลล่าสุด กรุณาลองรีเฟรชอีกครั้ง"}
              </div>
            )}

            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex max-w-full gap-2 overflow-x-auto pb-1" aria-label="กรองคิวตามสถานะ">
                <button
                  type="button"
                  className={`whitespace-nowrap rounded-full px-4 py-2 text-sm font-bold ${statusFilter === "active" ? "bg-[var(--brand)] text-white" : "border border-[var(--border)] bg-[var(--surface)]"}`}
                  aria-pressed={statusFilter === "active"}
                  onClick={() => setStatusFilter("active")}
                >
                  งานที่กำลังดำเนินการ
                </button>
                {statuses.map((status) => (
                  <button
                    key={status}
                    type="button"
                    className={`whitespace-nowrap rounded-full px-4 py-2 text-sm font-bold ${statusFilter === status ? "bg-[var(--brand)] text-white" : "border border-[var(--border)] bg-[var(--surface)]"}`}
                    aria-pressed={statusFilter === status}
                    onClick={() => setStatusFilter(status)}
                  >
                    {getStatusLabel(status)}
                  </button>
                ))}
              </div>
              {lastEventAt && (
                <p className="text-xs text-[var(--muted)]">
                  รับข้อมูลล่าสุด <time dateTime={lastEventAt.toISOString()}>{lastEventAt.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
                </p>
              )}
            </div>

            {loading ? (
              <Loading label="กำลังโหลดคิวออเดอร์…" fullPage={false} />
            ) : visibleOrders.length === 0 ? (
              <div className="surface-card p-10 text-center"><span className="text-4xl" aria-hidden="true">✓</span><h2 className="mt-4 text-xl font-black">ไม่มีออเดอร์ในมุมมองนี้</h2><p className="mt-2 text-[var(--muted)]">ออเดอร์ใหม่หรือการเปลี่ยนสถานะจะปรากฏอัตโนมัติ</p></div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {visibleOrders.map((order) => (
                  <article key={order.id} className={`surface-card p-5 sm:p-6 ${order.status === "pending" ? "border-l-4 border-l-amber-400" : ""}`}>
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <div className="flex flex-wrap items-center gap-3">
                          <StatusBadge status={order.status} />
                          <time className="text-xs text-[var(--muted)]" dateTime={order.created_at}>{formatOrderTime(order.created_at)}</time>
                        </div>
                        <h2 className="mt-4 font-black leading-7">{order.items.map((item) => `${item.quantity}× ${item.product_name_snapshot}`).join(", ")}</h2>
                        <p className="mt-2 text-xs text-[var(--muted)]">Order #{order.id.slice(-8).toUpperCase()}</p>
                      </div>
                      <p className="text-lg font-black text-[var(--brand)]">฿{order.total.toFixed(2)}</p>
                    </div>
                    {order.items.some((item) => item.note) && (
                      <div className="mt-4 rounded-xl bg-[var(--surface-muted)] px-4 py-3 text-sm">
                        <p className="font-bold">หมายเหตุ</p>
                        <ul className="mt-1 space-y-1 text-[var(--muted)]">
                          {order.items.filter((item) => item.note).map((item, index) => (
                            <li key={`${item.product_name_snapshot}-${index}`}>{item.product_name_snapshot}: {item.note}</li>
                          ))}
                        </ul>
                      </div>
                    )}
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
