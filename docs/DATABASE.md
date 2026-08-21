# DATABASE

Supabase PostgreSQL 스키마 설계. 명세 §54를 실제 DDL 수준으로 구체화한 것이다.
migration은 `supabase/migrations/`에 순번 SQL로 저장하고, 빈 프로젝트에서
`supabase db push` 한 번으로 전체 재현되어야 한다 (명세 §65).

원칙:

1. 모든 시각은 `timestamptz` (UTC). 일 단위는 `date`, 월 단위는 `text 'YYYY-MM'`.
2. `score_snapshots`는 **append-only** — UPDATE/DELETE를 트리거로 차단한다 (명세 §55).
3. enum은 PostgreSQL enum 타입 대신 `text` + `CHECK` 제약을 쓴다 (값 추가 시 migration
   부담 감소). 허용값의 단일 정의처는 `packages/shared/constants.json`이며 migration
   생성 시 여기서 CHECK 목록을 만든다.
4. `raw_payload`는 JSONB로 원문 그대로 보존, 최소 90일 (명세 §22).

---

## 1. 테이블 정의

### sources — Source Registry (명세 §6)

```
source_id       uuid PK default gen_random_uuid()
name            text NOT NULL UNIQUE          -- 'naver_news', 'naver_blog', ...
provider        text NOT NULL                 -- 'naver', 'google', 'korea.kr', ...
source_type     text NOT NULL CHECK           -- news|blog|cafe|video|trend|policy|demand
endpoint_type   text NOT NULL                 -- rest_json|rss|html
cadence         text NOT NULL CHECK           -- realtime|daily|monthly
collection_interval_minutes  int NOT NULL
expected_data_lag_minutes    int NOT NULL default 0   -- daily 소스는 일 단위 환산값
freshness_sla_minutes        jsonb NOT NULL   -- {green_lt, red_gte} 또는 {expected_lag_days}
required_for    jsonb NOT NULL default '[]'   -- ['market_gate','policy_gate','evergreen_gate']
enabled         boolean NOT NULL default true
priority        int NOT NULL default 100
official_source boolean NOT NULL default false
parser_version  text NOT NULL default 'v1'
config          jsonb NOT NULL default '{}'   -- endpoint url, root keyword 참조 등
created_at      timestamptz NOT NULL default now()
```

### workflow_runs (명세 §15)

```
id              uuid PK
workflow_name   text NOT NULL                 -- 'collect-market', ...
github_run_id   bigint                        -- dispatch/cron 실행이면 필수, 로컬 실행 시 NULL
github_sha      text
trigger_type    text NOT NULL CHECK           -- schedule|manual|test|local
scheduled_at    timestamptz                   -- cron 예정 시각 (manual이면 NULL)
started_at      timestamptz NOT NULL
completed_at    timestamptz
duration_seconds numeric
status          text NOT NULL CHECK           -- running|success|partial|failed
error_message   text
items_received  int default 0
items_new       int default 0

INDEX (workflow_name, started_at DESC)
```

### source_runs

```
id                  uuid PK
source_id           uuid NOT NULL FK→sources
workflow_run_id     uuid NOT NULL FK→workflow_runs
started_at          timestamptz NOT NULL
completed_at        timestamptz
status              text NOT NULL CHECK       -- running|success|failed
http_status         int
rows_received       int default 0
rows_new            int default 0
source_data_through timestamptz               -- DATA_FRESHNESS §2 규칙으로 계산된 값
error               text

INDEX (source_id, status, completed_at DESC)   -- last_success 조회 최적화
```

### source_items — Raw Data (명세 §22)

```
id              uuid PK
source_id       uuid NOT NULL FK→sources
external_id     text NOT NULL                 -- 소스별 규칙 (§4)
canonical_url   text
title           text NOT NULL
body_excerpt    text
author          text
published_at    timestamptz                   -- NULL 허용은 UNKNOWN precision만 (CHECK로 강제)
published_precision text NOT NULL CHECK       -- 'SECOND'|'MINUTE'|'DAY'|'UNKNOWN'  (§2 참조)
first_seen_at   timestamptz NOT NULL default now()  -- = 최초 fetched_at
fetched_at      timestamptz NOT NULL
raw_payload     jsonb NOT NULL
content_hash    text NOT NULL                 -- sha256(normalize(title)+normalize(excerpt))
language        text NOT NULL default 'ko'
source_type     text NOT NULL                 -- sources.source_type 복제 (조회 최적화)

UNIQUE (source_id, external_id)
INDEX (content_hash)
INDEX (source_type, published_at DESC)
INDEX (fetched_at)                             -- 90일 보관 정리용
```

### terms / term_mentions (명세 §54)

```
terms:
  id              uuid PK
  normalized_term text NOT NULL UNIQUE
  display_term    text NOT NULL
  first_seen_at   timestamptz NOT NULL
  last_seen_at    timestamptz NOT NULL
  category        text

term_mentions:
  term_id         uuid FK→terms
  source_item_id  uuid FK→source_items
  published_at    timestamptz                  -- item의 published_at 복제 (윈도우 집계용)
  published_precision text NOT NULL            -- item에서 복제 (6h 필터용, §2)
  effective_at    timestamptz NOT NULL         -- 집계 기준 시각 (§2의 effective_at 규칙)
  source_type     text NOT NULL
  PK (term_id, source_item_id)
  INDEX (term_id, effective_at DESC)
```

### candidates / candidate_metrics

```
candidates:
  id              uuid PK
  primary_term_id uuid FK→terms
  cluster_name    text NOT NULL
  candidate_type  text NOT NULL CHECK          -- market|policy|evergreen
  lifecycle       text NOT NULL CHECK          -- new|rising|now|watch|late|mature|expired
  category        text NOT NULL                -- product|tech|life|policy|finance|...
  created_rule    text NOT NULL                -- 'A'|'B'|'C'|'D' (명세 §24 어느 규칙으로 생성됐나)
  first_now_at    timestamptz                  -- Prediction Log (명세 §56)
  created_at / updated_at timestamptz

candidate_metrics:                              -- append-only 권장 (윈도우별 1행)
  candidate_id    uuid FK→candidates
  window_start    timestamptz NOT NULL
  window_end      timestamptz NOT NULL
  mentions        int
  distinct_documents int
  distinct_sources   int                        -- syndication cluster 반영 후 (명세 §29)
  velocity        numeric
  acceleration    numeric
  novelty         numeric
  search_trend_ratio  numeric                   -- DataLab 최신 ratio
  monthly_search  int                           -- Search Ads ("<10"은 5로 저장 + 원문은 raw에)
  content_supply  jsonb                         -- {blog_24h, blog_7d, cafe_24h, ratio_7d}
  PK (candidate_id, window_start, window_end)
```

### score_snapshots — append-only (명세 §55)

```
id                    uuid PK
candidate_id          uuid FK→candidates
score_version         text NOT NULL            -- config/scoring.v1.json 버전
calculated_at         timestamptz NOT NULL
data_complete_through timestamptz NOT NULL
early_signal          numeric NOT NULL
opportunity           numeric NOT NULL
confidence            numeric NOT NULL
rank_score            numeric NOT NULL         -- opportunity*(0.70+0.30*confidence/100), §35
freshness_pass        boolean NOT NULL
components            jsonb NOT NULL           -- 개별 점수 성분 + source_status 스냅샷 + 가중치

INDEX (candidate_id, calculated_at DESC)
TRIGGER: BEFORE UPDATE OR DELETE → RAISE EXCEPTION  -- 덮어쓰기 원천 차단
```

### candidate_evidence / content_briefs / experience_answers / drafts / monetization

명세 §54 그대로. 추가 결정사항만 기록:

```
candidate_evidence:  PK (candidate_id, source_item_id)
                     evidence_type CHECK: mention|trend|policy_event|demand
content_briefs:      UNIQUE (candidate_id, version); brief_json에 §46 구조
experience_answers:  brief_id FK, question_order int, question text, answer text,
                     answered_at timestamptz
drafts:              destination CHECK: naver|web
                     status CHECK: draft|naver_ready|published|discarded
monetization:        route CHECK: naver_shopping|naver_traffic|web_adsense|dual
                     shopping_connect_url text  -- 사용자 수동 입력만 (명세 §8)
feedback:            candidate_id FK, verdict CHECK: good|bad|missed|wrote,
                     created_at  -- Precision@N 계산용 (명세 §57)
```

### system_health / alerts (명세 §54, §17)

```
system_health:
  component     text PK                        -- 소스명 또는 워크플로명
  status        text NOT NULL CHECK            -- green|yellow|red
  last_success_at timestamptz
  data_through  timestamptz
  checked_at    timestamptz NOT NULL
  message       text

alerts:
  id UUID PK, severity CHECK(info|warn|red), component, message,
  created_at, resolved_at
  부분 UNIQUE INDEX (component) WHERE resolved_at IS NULL   -- 미해소 중복 방지
```

### policy_events — 정책 상태 분리 (명세 §13, §48)

정당·정부 자료의 유형 혼동 금지를 스키마로 강제한다:

```
policy_events:
  id uuid PK
  source_item_id uuid FK→source_items
  event_type text NOT NULL CHECK (
    'PARTY_PROPOSAL','PARTY_PLEDGE','PARTY_POSITION','BILL_PROPOSED',
    'GOVERNMENT_ANNOUNCEMENT','LEGISLATIVE_NOTICE','ENACTED','EFFECTIVE')
  announced_at date
  effective_at date                            -- 시행일 (있는 경우)
  notice_period daterange                      -- 입법/행정예고 기간
  ministry text
  is_confirmed boolean NOT NULL                -- ENACTED/EFFECTIVE만 true 가능 (CHECK)
```

---

## 2. published_precision — 시간 해상도 규칙 (확정)

`published_precision`은 4값 enum이다: **`SECOND` | `MINUTE` | `DAY` | `UNKNOWN`**.

2026-08 공식 문서 검증 결과의 매핑 ([DATA_SOURCES.md](DATA_SOURCES.md) §1):

- News `pubDate`: 초 단위 시각 → `SECOND`
- Google Trends RSS `pubDate` / YouTube `publishedAt`: → `SECOND` (RSS는 분 해상도면 `MINUTE`)
- Blog `postdate`: **날짜만 (yyyymmdd)** → `DAY`, published_at은 KST 자정으로 저장
- Cafe: **게시시각 필드 없음** → `UNKNOWN`, published_at NULL

### 핵심 규칙 — 정밀도 혼합 계산 금지

**`DAY` / `UNKNOWN` 데이터는 6h velocity / acceleration의 primary evidence로
사용하지 않는다.** 날짜 단위 데이터를 6시간 단위 데이터처럼 취급하는 것을 금지한다.

| precision | 6h 윈도우 (velocity/acceleration) | 24h+ 윈도우 / daily supply | cross-source evidence | effective_at |
| --- | --- | --- | --- | --- |
| SECOND / MINUTE | **포함** | 포함 | 포함 | published_at |
| DAY | **제외** | 포함 (날짜는 정확) | 포함 | published_at (KST 자정) |
| UNKNOWN | **제외** | 포함 (first_seen 기준) | 포함 | first_seen_at |

- 6h/prev-6h 카운트와 그로부터 파생되는 velocity·acceleration은
  `precision IN ('SECOND','MINUTE')`인 mention만으로 계산한다.
- DAY/UNKNOWN mention은 24h 이상 윈도우, content supply(blog/cafe 공급량),
  cross-source·evidence 카운트에만 기여한다.
- 이 필터는 scoring 코드에 하드코딩된 WHERE 조건이며 unit test로 고정한다
  ([ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md) §1.1 precision 테스트).

"Source timestamp 없는 데이터 금지"(명세 §64)의 적용: Cafe는 API가 구조적으로
미제공임이 공식 문서로 확인된 예외이며, `UNKNOWN`으로 그 사실이 데이터에 남는다.
Cafe 문서는 evidence 표시 시 게시시각을 "미제공"으로 표기한다.

---

## 3. 보존 정책

- `source_items`: 90일 경과분은 `raw_payload`만 NULL 처리(메타데이터·hash는 유지) —
  Supabase Cron 일일 job. 완전 삭제는 하지 않는다 (evidence 링크 보존).
- `workflow_runs`/`source_runs`: 180일.
- `score_snapshots`: 무기한 (감사·Precision 측정의 원천).
- pg_cron `cron.job_run_details`: 30일 (디스크 관리, 공식 권고).

## 4. external_id 규칙 (dedupe 1차 키)

| 소스 | external_id |
| --- | --- |
| Naver News/Blog/Cafe | link URL의 정규화 값 (쿼리스트링 제거) |
| Google Trends | `{title}:{pubDate date}` |
| YouTube | videoId |
| 정책브리핑/법령/예고 | API가 주는 고유 id (없으면 상세 URL) |

2차 dedupe는 `content_hash` (제목+발췌 정규화 sha256) — 동일 사건의 재발행/신디케이션
탐지 (명세 §29의 syndication cluster 판정 입력).

## 5. Views (대시보드는 이 view만 읽는다)

```
v_system_health   : system_health + sources 조인, SLA 대비 나이 계산 포함
v_data_cutoff     : DATA_FRESHNESS §4의 계산식 그대로 (단일 행)
v_workflow_recent : 워크플로별 최근 20 run + source_runs 요약
v_today           : 최신 snapshot 기준 NOW/WATCH 후보 + freshness 재평가 입력값
v_candidate_detail: 후보별 evidence/metrics/score 히스토리
```

## 6. RLS 요약 (상세는 [SECURITY.md](SECURITY.md))

- 기본: 모든 테이블 RLS ON, anon은 위 view의 SELECT만.
- anon 쓰기 허용은 정확히 3가지: `feedback` INSERT, `experience_answers` INSERT,
  `monetization.shopping_connect_url` UPDATE (그 컬럼만, RPC 함수로 한정).
- service_role(워커 전용)은 전권. 프론트엔드에 절대 노출 금지.
- V1은 단일 사용자 시스템이며 대시보드 자체의 접근 제어(Vercel 인증 등)는
  Open Decision.
