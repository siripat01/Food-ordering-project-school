type LoadingProps = {
  label?: string;
  fullPage?: boolean;
};

export default function Loading({
  label = "กำลังเตรียมข้อมูล…",
  fullPage = true,
}: LoadingProps) {
  return (
    <div
      className={`grid place-items-center px-6 ${fullPage ? "min-h-[70vh]" : "min-h-40"}`}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-4 text-center text-[var(--muted)]">
        <span
          className="h-10 w-10 animate-spin rounded-full border-4 border-[#d8e5dc] border-t-[var(--brand)]"
          aria-hidden="true"
        />
        <span className="text-sm font-semibold">{label}</span>
      </div>
    </div>
  );
}
