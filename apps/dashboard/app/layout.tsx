import type { Metadata } from "next";
import Link from "next/link";
import { envBadgeStyle, radarEnv } from "@/lib/env";
import "./globals.css";

export const metadata: Metadata = { title: "Content Radar" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const env = radarEnv();
  return (
    <html lang="ko">
      <body>
        <nav className="border-b border-neutral-200 bg-white">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm">
            <span className="font-bold tracking-wide">CONTENT RADAR</span>
            {/* 환경 배지 (M1.5 §11) — 모든 화면 고정 */}
            <span className={`rounded px-2 py-0.5 text-xs font-bold tracking-wider ${envBadgeStyle[env]}`}>
              {env}
            </span>
            <Link href="/today" className="text-neutral-600 hover:text-neutral-900">TODAY</Link>
            <Link href="/health" className="text-neutral-600 hover:text-neutral-900">SOURCES / HEALTH</Link>
            {process.env.AUTH_DISABLED === "true" && (
              <span className="ml-auto rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
                AUTH DISABLED (local/mock)
              </span>
            )}
          </div>
        </nav>
        {env === "MOCK" && (
          <div className="border-b border-amber-200 bg-amber-50 py-1 text-center text-xs font-medium text-amber-900">
            MOCK 환경 — 아래 데이터는 전부 시뮬레이션이며 실제 추천이 아닙니다
          </div>
        )}
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
