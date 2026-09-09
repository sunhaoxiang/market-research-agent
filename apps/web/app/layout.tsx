import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Market Research Agent",
  description: "AI Financial Research Platform — Crypto & US Stocks",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        <a
          href="#main"
          className="focus:bg-accent focus:text-accent-contrast sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:px-3 focus:py-2 focus:text-sm"
        >
          跳到主要内容
        </a>
        {children}
      </body>
    </html>
  );
}
