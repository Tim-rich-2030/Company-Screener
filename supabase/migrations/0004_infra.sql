-- Content Radar — 0004: Milestone 1.5 인프라 검증 구조
-- (1) GitHub Actions → Supabase 연결성 검사 전용 테이블 (운영 테이블 오염 방지)
-- (2) live/mock 데이터 구분 view: trigger_type='test'/'local' run은 live로 치지 않는다

create table infra_connectivity_checks (
  id            uuid primary key default gen_random_uuid(),
  github_run_id bigint,
  note          text,
  checked_at    timestamptz not null default now()
);
alter table infra_connectivity_checks enable row level security;

-- 소스별 "실제(live) 수집 성공" — mock/test 실행 제외
create or replace view v_source_last_live_success as
select distinct on (sr.source_id)
  sr.source_id, sr.completed_at as last_live_success_at, sr.source_data_through
from source_runs sr
join workflow_runs wr on wr.id = sr.workflow_run_id
where sr.status = 'success' and wr.trigger_type in ('schedule', 'manual')
order by sr.source_id, sr.completed_at desc;

-- 인프라 상태 단일 행: live 데이터 연결 여부 + 마지막 실제 GitHub workflow 성공
create or replace view v_infra_status as
select
  exists (select 1 from v_source_last_live_success) as live_data_connected,
  (select wr.workflow_name from workflow_runs wr
    where wr.status in ('success','partial') and wr.trigger_type in ('schedule','manual')
    order by wr.completed_at desc nulls last limit 1)  as last_live_workflow_name,
  (select wr.completed_at from workflow_runs wr
    where wr.status in ('success','partial') and wr.trigger_type in ('schedule','manual')
    order by wr.completed_at desc nulls last limit 1)  as last_live_workflow_at,
  (select wr.github_run_id from workflow_runs wr
    where wr.status in ('success','partial') and wr.trigger_type in ('schedule','manual')
    order by wr.completed_at desc nulls last limit 1)  as last_live_github_run_id;

-- v_system_health에 live 성공 시각 추가 (NOT CONNECTED 판정용)
-- 컬럼 순서가 바뀌므로 replace 불가 → drop 후 재생성
drop view v_system_health;
create view v_system_health as
select
  s.name,
  s.source_type,
  s.cadence,
  s.enabled,
  s.published_precision,
  s.freshness_sla_minutes,
  coalesce(h.status, 'RED')  as status,
  h.checked_at,
  h.message,
  ls.last_success_at,
  ls.source_data_through     as data_through,
  lls.last_live_success_at,
  lr.last_run_at,
  lr.last_run_status,
  lr.rows_received,
  lr.rows_new,
  lr.error                   as last_error
from sources s
left join system_health h            on h.component = s.name
left join v_source_last_success ls   on ls.source_id = s.source_id
left join v_source_last_live_success lls on lls.source_id = s.source_id
left join v_source_last_run lr       on lr.source_id = s.source_id
where s.enabled;

grant select on v_infra_status, v_source_last_live_success, v_system_health
  to authenticated;
