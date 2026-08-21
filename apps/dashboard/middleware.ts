// 인증 skeleton (확정 결정: Supabase Auth + 관리자 이메일 allowlist).
// 원칙: 기본 거부(fail-closed). Supabase Auth가 구성되지 않은 배포는
// AUTH_DISABLED=true(로컬/mock 전용)가 아닌 한 아무 화면도 열리지 않는다.
import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";

const PUBLIC_PATHS = ["/login", "/unavailable"];

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p)) || pathname.startsWith("/_next")) {
    return NextResponse.next();
  }

  // 로컬 개발/mock 전용 우회 — 운영 배포에서 절대 설정 금지 (docs/SECURITY.md §2)
  if (process.env.AUTH_DISABLED === "true") {
    return NextResponse.next();
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    // 인증 미구성 → 공개 배포 금지 원칙에 따라 차단
    return NextResponse.rewrite(new URL("/unavailable", req.url));
  }

  const res = NextResponse.next();
  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll: () => req.cookies.getAll(),
      setAll: (cookies) =>
        cookies.forEach(({ name, value, options }) => res.cookies.set(name, value, options)),
    },
  });
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email?.toLowerCase();

  const admins = (process.env.ADMIN_EMAILS ?? "")
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);

  if (!email || !admins.includes(email)) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return res;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
