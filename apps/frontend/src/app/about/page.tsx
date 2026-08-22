"use client";

import { motion } from "motion/react";
import Navbar from "../components/Navbar";

export default function AboutPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <motion.section
        initial={{ opacity: 0, translateY: 16 }}
        animate={{ opacity: 1, translateY: 0 }}
        className="page-shell grid min-h-[70vh] place-items-center py-16"
      >
        <div className="surface-card max-w-3xl p-8 sm:p-12">
          <p className="eyebrow">About this project</p>
          <h1 className="mt-3 text-4xl font-black tracking-tight">จากโปรเจกต์ในห้องเรียน สู่ระบบสั่งอาหารที่ใช้งานได้จริง</h1>
          <p className="mt-6 text-lg leading-8 text-[var(--muted)]">
            หิวข้าวเป็นโปรเจกต์สำหรับฝึกพัฒนาซอฟต์แวร์แบบครบวงจร ตั้งแต่การเลือกเมนู การยืนยันตัวตน ไปจนถึงการจัดการคิวของร้าน โดยให้ความสำคัญกับความปลอดภัยและประสบการณ์ใช้งานที่เข้าใจง่าย
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            {["Customer-friendly ordering", "Role-aware operations", "Server-verified pricing"].map((item) => (
              <div key={item} className="rounded-2xl bg-[#eef2eb] p-4 text-sm font-bold text-[var(--brand)]">{item}</div>
            ))}
          </div>
        </div>
      </motion.section>
    </main>
  );
}
