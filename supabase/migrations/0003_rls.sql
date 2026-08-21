-- Content Radar — 0003: RLS 및 접근 제어 skeleton
-- 확정 결정: Supabase Auth + 관리자 이메일 allowlist. anon에게는 아무것도 열지 않는다.
-- (docs/SECURITY.md §2)

-- Supabase에는 anon/authenticated 롤이 이미 존재. 로컬 Postgres(mock/test)에서는 생성.
do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
end $$;

-- 모든 테이블 RLS ON (정책 없음 = service_role 외 접근 불가)
alter table sources            enable row level security;
alter table workflow_runs      enable row level security;
alter table source_runs        enable row level security;
alter table source_items       enable row level security;
alter table terms              enable row level security;
alter table term_mentions      enable row level security;
alter table candidates         enable row level security;
alter table candidate_metrics  enable row level security;
alter table score_snapshots    enable row level security;
alter table candidate_evidence enable row level security;
alter table system_health      enable row level security;
alter table alerts             enable row level security;

-- 관리자 allowlist (Supabase Auth 이메일 기준)
create table admin_users (
  email      text primary key,
  created_at timestamptz not null default now()
);
alter table admin_users enable row level security;

-- 로그인한 관리자에게만 view 읽기 허용.
-- view는 기본 security definer 의미(소유자 권한)로 실행되므로 테이블 RLS를 우회해
-- 읽기 전용 집계만 노출한다. anon에는 아무 grant도 주지 않는다.
grant usage on schema public to authenticated;
grant select on v_system_health, v_data_cutoff, v_workflow_recent,
                v_today, v_candidate_evidence, v_candidate_metrics,
                v_latest_snapshot, v_source_last_success, v_source_last_run
  to authenticated;

-- 참고: 관리자 여부의 최종 강제는 Supabase 환경에서 auth.jwt() 기반 정책으로 조인다.
-- 로컬 Postgres에는 auth 스키마가 없으므로 이 migration에서는 롤 grant까지만 두고,
-- Supabase 적용 시 0004_supabase_auth.sql(추후)에서
--   using (exists (select 1 from admin_users a where a.email = auth.jwt()->>'email'))
-- 형태의 정책과 view의 security_invoker 전환을 적용한다. 그 전까지 접근 통제는
-- 대시보드 미들웨어(로그인 + ADMIN_EMAILS 검사)가 담당한다.
