"use client";
// 관리자 로그인 skeleton — Supabase Auth (이메일/비밀번호).
// 로그인해도 ADMIN_EMAILS에 없는 계정은 middleware가 다시 이 페이지로 돌려보낸다.
import { useState } from "react";
import { createBrowserClient } from "@supabase/ssr";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const configured = Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const supabase = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
    );
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setError(error.message);
    else window.location.href = "/today";
  }

  return (
    <div className="mx-auto max-w-sm py-16">
      <h1 className="text-lg font-bold">관리자 로그인</h1>
      <p className="mt-1 text-xs text-neutral-500">
        지정된 관리자 이메일 계정만 접근할 수 있습니다.
      </p>
      {!configured ? (
        <p className="mt-6 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          Supabase Auth 미구성 상태입니다. 로컬 개발은 AUTH_DISABLED=true로 실행하세요.
        </p>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-3">
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="email" className="w-full rounded border border-neutral-300 px-3 py-2 text-sm" />
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="password" className="w-full rounded border border-neutral-300 px-3 py-2 text-sm" />
          {error && <p className="text-sm text-red-700">{error}</p>}
          <button type="submit" className="w-full rounded bg-neutral-900 py-2 text-sm text-white">
            로그인
          </button>
        </form>
      )}
    </div>
  );
}
