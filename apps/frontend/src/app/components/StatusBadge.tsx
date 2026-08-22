export type OrderStatus =
  | "pending"
  | "confirmed"
  | "preparing"
  | "ready"
  | "completed"
  | "cancelled";

const statusStyles: Record<OrderStatus, { label: string; classes: string }> = {
  pending: { label: "รอยืนยัน", classes: "bg-amber-50 text-amber-800 ring-amber-200" },
  confirmed: { label: "ยืนยันแล้ว", classes: "bg-blue-50 text-blue-800 ring-blue-200" },
  preparing: { label: "กำลังปรุง", classes: "bg-orange-50 text-orange-800 ring-orange-200" },
  ready: { label: "พร้อมรับ", classes: "bg-emerald-50 text-emerald-800 ring-emerald-200" },
  completed: { label: "สำเร็จ", classes: "bg-slate-100 text-slate-700 ring-slate-200" },
  cancelled: { label: "ยกเลิก", classes: "bg-rose-50 text-rose-800 ring-rose-200" },
};

export function getStatusLabel(status: OrderStatus) {
  return statusStyles[status].label;
}

export default function StatusBadge({ status }: { status: OrderStatus }) {
  const config = statusStyles[status];
  return (
    <span
      className={`inline-flex w-fit items-center rounded-full px-3 py-1 text-xs font-bold ring-1 ring-inset ${config.classes}`}
    >
      {config.label}
    </span>
  );
}
