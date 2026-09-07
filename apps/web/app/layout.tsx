import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Market Research Agent",
  description: "AI Financial Research Platform — Crypto & US Stocks",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
