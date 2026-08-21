-- Content Radar — 0002: 대시보드용 view
-- 대시보드는 이 view만 읽는다 (docs/DATABASE.md §5).

-- 소스별 마지막 성공 run (재사용 서브쿼리)
create or replace view v_source_last_success as
select distinct on (sr.source_id)
  sr.source_id, sr.completed_at as last_success_at, sr.source_data_through,
  sr.rows_received, sr.rows_new
from source_runs sr
where sr.status = 'success'
order by sr.source_id, sr.completed_at desc;

create or replace view v_source_last_run as
select distinct on (sr.source_id)
  sr.source_id, sr.started_at as last_run_at, sr.status as last_run_status,
  sr.rows_received, sr.rows_new, sr.error
from source_runs sr
order by sr.source_id, sr.started_at desc;

-- SOURCES / HEALTH 화면의 원천
create or replace view v_system_health as
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
  lr.last_run_at,
  lr.last_run_status,
  lr.rows_received,
  lr.rows_new,
  lr.error                   as last_error
from sources s
left join system_health h        on h.component = s.name
left join v_source_last_success ls on ls.source_id = s.source_id
left join v_source_last_run lr     on lr.source_id = s.source_id
where s.enabled;

-- Global Data Cutoff (docs/DATA_FRESHNESS.md §4) — 단일 행
create or replace view v_data_cutoff as
select
  (select min(ls.source_data_through)
     from sources s left join v_source_last_success ls on ls.source_id = s.source_id
    where s.enabled and s.name in ('naver_news','naver_blog','naver_cafe','google_trends'))
    as market_complete_through,
  (select min(ls.source_data_through)
     from sources s left join v_source_last_success ls on ls.source_id = s.source_id
    where s.enabled and s.source_type = 'policy')
    as policy_complete_through,
  (select ls.source_data_through
     from sources s join v_source_last_success ls on ls.source_id = s.source_id
    where s.name = 'naver_search_trend')
    as search_trend_data_through,
  (select max(completed_at) from workflow_runs
    where workflow_name = 'score-and-rank' and status in ('success','partial'))
    as last_pipeline_at,
  (select count(*) from system_health h join sources s on s.name = h.component
    where s.enabled and h.status = 'GREEN')
    as sources_green,
  (select count(*) from sources where enabled)
    as sources_total,
  (select case when exists (select 1 from system_health h
                             join sources s on s.name = h.component
                            where s.enabled and h.status = 'RED')
               then 'RED'
               when exists (select 1 from system_health h
                             join sources s on s.name = h.component
                            where s.enabled and h.status = 'YELLOW')
               then 'YELLOW' else 'GREEN' end)
    as overall_status;

-- 워크플로 최근 실행
create or replace view v_workflow_recent as
select wr.*,
  (select count(*) from source_runs sr where sr.workflow_run_id = wr.id)               as source_count,
  (select count(*) from source_runs sr where sr.workflow_run_id = wr.id
     and sr.status = 'failed')                                                          as source_failed
from workflow_runs wr
order by wr.started_at desc;

-- 후보별 최신 snapshot
create or replace view v_latest_snapshot as
select distinct on (candidate_id) *
from score_snapshots
order by candidate_id, calculated_at desc;

-- TODAY 화면의 원천
create or replace view v_today as
select
  c.id as candidate_id,
  c.cluster_name,
  c.candidate_type,
  c.lifecycle,
  c.category,
  t.display_term,
  ss.opportunity,
  ss.confidence,
  ss.rank_score,
  ss.early_signal,
  ss.freshness_pass,
  ss.calculated_at,
  ss.data_complete_through,
  ss.score_version,
  ss.components
from candidates c
join terms t on t.id = c.primary_term_id
join v_latest_snapshot ss on ss.candidate_id = c.id
order by ss.rank_score desc;

-- Candidate Detail: evidence 목록
create or replace view v_candidate_evidence as
select
  ce.candidate_id,
  ce.evidence_type,
  ce.weight,
  si.title,
  si.canonical_url,
  si.published_at,
  si.published_precision,
  si.fetched_at,
  si.first_seen_at,
  s.name as source_name,
  s.source_type,
  coalesce(h.status, 'RED') as source_status
from candidate_evidence ce
join source_items si on si.id = ce.source_item_id
join sources s on s.source_id = si.source_id
left join system_health h on h.component = s.name;

-- Candidate Detail: 최근 metrics
create or replace view v_candidate_metrics as
select * from candidate_metrics order by window_end desc;
