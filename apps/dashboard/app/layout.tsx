import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Content Radar" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <nav className="border-b border-neutral-200 bg-white">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm">
            <span className="font-bold tracking-wide">CONTENT RADAR</span>
            <Link href="/today" className="text-neutral-600 hover:text-neutral-900">TODAY</Link>
            <Link href="/health" className="text-neutral-600 hover:text-neutral-900">SOURCES / HEALTH</Link>
            {process.env.AUTH_DISABLED === "true" && (
              <span className="ml-auto rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
                AUTH DISABLED (local/mock)
              </span>
            )}
          </div>
        </nav>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
