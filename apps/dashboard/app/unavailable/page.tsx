export default function Unavailable() {
  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <h1 className="text-lg font-bold">대시보드 비활성</h1>
      <p className="mt-3 text-sm text-neutral-600">
        Supabase Auth가 구성되지 않아 접근이 차단되었습니다. 이 대시보드는 인증 없이
        공개 배포되지 않습니다 (fail-closed). 관리자는 NEXT_PUBLIC_SUPABASE_URL /
        NEXT_PUBLIC_SUPABASE_ANON_KEY / ADMIN_EMAILS 환경변수를 설정하세요.
      </p>
    </div>
  );
}
