import Navbar from "../components/Navbar";

export default function ContactPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <section className="page-shell grid min-h-[70vh] place-items-center py-16">
        <div className="surface-card grid w-full max-w-3xl gap-8 overflow-hidden p-7 sm:grid-cols-[1fr_auto] sm:items-center sm:p-10">
          <div>
            <p className="eyebrow">Contact us</p>
            <h1 className="mt-3 text-4xl font-black tracking-tight">คุยกับเราผ่าน LINE</h1>
            <p className="mt-4 max-w-md leading-7 text-[var(--muted)]">สแกน QR Code หรือเพิ่มเพื่อนด้วย LINE ID เพื่อเริ่มพูดคุยกับผู้ช่วยสั่งอาหาร</p>
            <p className="mt-6 inline-flex rounded-full bg-[#eef6f1] px-4 py-2 font-black text-[var(--brand)]">LINE ID: @610xgrek</p>
          </div>
          <div className="mx-auto rounded-2xl border border-[var(--border)] bg-white p-4">
            {/* This QR code is hosted by the restaurant's LINE Official Account. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="https://qr-official.line.me/sid/L/610xgrek.png" alt="QR Code สำหรับเพิ่มเพื่อน LINE @610xgrek" className="h-52 w-52" />
          </div>
        </div>
      </section>
    </main>
  );
}
