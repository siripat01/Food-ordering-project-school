import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "หิวข้าว — Food Ordering",
    template: "%s | หิวข้าว",
  },
  description: "สั่งอาหารอย่างปลอดภัยและติดตามสถานะได้ทุกขั้นตอน",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="th">
      <body>{children}</body>
    </html>
  );
}
