-- Content Radar — 0001: 핵심 스키마
-- 설계 근거: docs/DATABASE.md. 모든 시각은 timestamptz(UTC).

create extension if not exists pgcrypto;

-- ── Source Registry ─────────────────────────────────────────────
create table sources (
  source_id                   uuid primary key default gen_random_uuid(),
  name                        text not null unique,
  provider                    text not null,
  source_type                 text not null check (source_type in
                                ('news','blog','cafe','video','trend','policy','demand')),
  endpoint_type               text not null,
  cadence                     text not null check (cadence in ('realtime','daily','monthly')),
  collection_interval_minutes int  not null,
  expected_data_lag_minutes   int  not null default 0,
  freshness_sla_minutes       jsonb not null default '{}',
  published_precision         text not null check (published_precision in
                                ('SECOND','MINUTE','DAY','UNKNOWN')),
  required_for                jsonb not null default '[]',
  enabled                     boolean not null default true,
  priority                    int not null default 100,
  official_source             boolean not null default false,
  parser_version              text not null default 'v1',
  config                      jsonb not null default '{}',
  created_at                  timestamptz not null default now()
);

-- ── 실행 기록 ────────────────────────────────────────────────────
create table workflow_runs (
  id               uuid primary key default gen_random_uuid(),
  workflow_name    text not null,
  github_run_id    bigint,
  github_sha       text,
  trigger_type     text not null check (trigger_type in ('schedule','manual','test','local')),
  scheduled_at     timestamptz,
  started_at       timestamptz not null,
  completed_at     timestamptz,
  duration_seconds numeric,
  status           text not null check (status in ('running','success','partial','failed')),
  error_message    text,
  items_received   int not null default 0,
  items_new        int not null default 0
);
create index workflow_runs_name_started on workflow_runs (workflow_name, started_at desc);

create table source_runs (
  id                  uuid primary key default gen_random_uuid(),
  source_id           uuid not null references sources(source_id),
  workflow_run_id     uuid not null references workflow_runs(id),
  started_at          timestamptz not null,
  completed_at        timestamptz,
  status              text not null check (status in ('running','success','failed')),
  http_status         int,
  rows_received       int not null default 0,
  rows_new            int not null default 0,
  source_data_through timestamptz,
  error               text
);
create index source_runs_last_success on source_runs (source_id, status, completed_at desc);

-- ── Raw Data ────────────────────────────────────────────────────
create table source_items (
  id                  uuid primary key default gen_random_uuid(),
  source_id           uuid not null references sources(source_id),
  external_id         text not null,
  canonical_url       text,
  title               text not null,
  body_excerpt        text,
  author              text,
  published_at        timestamptz,
  published_precision text not null check (published_precision in
                        ('SECOND','MINUTE','DAY','UNKNOWN')),
  first_seen_at       timestamptz not null default now(),
  fetched_at          timestamptz not null,
  raw_payload         jsonb not null default '{}',
  content_hash        text not null,
  language            text not null default 'ko',
  source_type         text not null,
  unique (source_id, external_id),
  -- published_at 없는 저장은 UNKNOWN precision만 허용 (명세 §64 + DATABASE.md §2)
  constraint published_at_required check
    (published_at is not null or published_precision = 'UNKNOWN')
);
create index source_items_hash on source_items (content_hash);
create index source_items_type_published on source_items (source_type, published_at desc);
create index source_items_fetched on source_items (fetched_at);

-- ── Terms ───────────────────────────────────────────────────────
create table terms (
  id              uuid primary key default gen_random_uuid(),
  normalized_term text not null unique,
  display_term    text not null,
  first_seen_at   timestamptz not null,
  last_seen_at    timestamptz not null,
  category        text
);

create table term_mentions (
  term_id             uuid not null references terms(id),
  source_item_id      uuid not null references source_items(id),
  published_at        timestamptz,
  published_precision text not null check (published_precision in
                        ('SECOND','MINUTE','DAY','UNKNOWN')),
  effective_at        timestamptz not null,
  source_type         text not null,
  primary key (term_id, source_item_id)
);
create index term_mentions_effective on term_mentions (term_id, effective_at desc);

-- ── Candidates ──────────────────────────────────────────────────
create table candidates (
  id              uuid primary key default gen_random_uuid(),
  primary_term_id uuid not null references terms(id),
  cluster_name    text not null,
  candidate_type  text not null check (candidate_type in ('market','policy','evergreen')),
  lifecycle       text not null check (lifecycle in
                    ('new','rising','now','watch','late','mature','expired')),
  category        text not null,
  created_rule    text not null check (created_rule in ('A','B','C','D')),
  first_now_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table candidate_metrics (
  candidate_id       uuid not null references candidates(id),
  window_start       timestamptz not null,
  window_end         timestamptz not null,
  mentions           int,
  distinct_documents int,
  distinct_sources   int,
  velocity           numeric,
  acceleration       numeric,
  novelty            numeric,
  search_trend_ratio numeric,
  monthly_search     int,
  content_supply     jsonb not null default '{}',
  primary key (candidate_id, window_start, window_end)
);

-- ── Score Snapshots (append-only) ───────────────────────────────
create table score_snapshots (
  id                    uuid primary key default gen_random_uuid(),
  candidate_id          uuid not null references candidates(id),
  score_version         text not null,
  calculated_at         timestamptz not null,
  data_complete_through timestamptz,
  early_signal          numeric not null,
  opportunity           numeric not null,
  confidence            numeric not null,
  rank_score            numeric not null,
  freshness_pass        boolean not null,
  components            jsonb not null default '{}'
);
create index score_snapshots_candidate on score_snapshots (candidate_id, calculated_at desc);

create or replace function forbid_snapshot_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'score_snapshots is append-only (spec §55): % not allowed', tg_op;
end $$;

create trigger score_snapshots_append_only
  before update or delete on score_snapshots
  for each row execute function forbid_snapshot_mutation();

-- ── Evidence ────────────────────────────────────────────────────
create table candidate_evidence (
  candidate_id   uuid not null references candidates(id),
  source_item_id uuid not null references source_items(id),
  evidence_type  text not null check (evidence_type in
                   ('mention','trend','policy_event','demand')),
  weight         numeric not null default 1,
  primary key (candidate_id, source_item_id)
);

-- ── Health / Alerts ─────────────────────────────────────────────
create table system_health (
  component       text primary key,
  status          text not null check (status in ('GREEN','YELLOW','RED')),
  last_success_at timestamptz,
  data_through    timestamptz,
  checked_at      timestamptz not null,
  message         text
);

create table alerts (
  id          uuid primary key default gen_random_uuid(),
  severity    text not null check (severity in ('info','warn','red')),
  component   text not null,
  message     text not null,
  created_at  timestamptz not null default now(),
  resolved_at timestamptz
);
create unique index alerts_open_component on alerts (component) where resolved_at is null;
