# ARCHITECTURE

Content Radar V1의 전체 구조. 제품 요구사항의 원본은 [specs/content-radar-v1.md](../specs/content-radar-v1.md)이며,
이 문서는 그것을 실행 가능한 시스템 구조로 옮긴 것이다. 충돌 시 명세서가 우선한다.

## 1. 설계를 지배하는 4가지 제약

1. **AI는 점수를 만들지 않는다** — 모든 수치 계산은 `workers/scoring`의 순수 Python 함수.
   Claude 호출은 `workers/content`와 discovery의 클러스터링 보조에만 존재한다.
   Claude API가 전부 죽어도 수집→점수→랭킹→대시보드는 완전 정상 동작해야 한다 (명세 §61 Test E).
2. **Fail-Closed** — 필수 소스가 stale이면 TODAY 추천을 중단한다. 랭킹 계산은 계속하되
   `freshness_pass=false`로 저장하고 화면에 노출하지 않는다 (명세 §3, §21).
3. **실행 인프라를 신뢰하지 않는다** — GitHub Actions의 cron은 지연·누락될 수 있고,
   자기 실패를 자기가 보고할 수 없다. 따라서 실행 증적은 3중으로 남긴다:
   DB(`workflow_runs`) + Healthchecks.io heartbeat + Supabase Cron watchdog.
   상세는 [WORKFLOWS.md](WORKFLOWS.md).
4. **세 개의 시간 분리** — `fetched_at` / `published_at` / `source_data_through`.
   상세는 [DATA_FRESHNESS.md](DATA_FRESHNESS.md).

## 2. 컴포넌트 지도

```
┌────────────────────────── GitHub Actions (cron, UTC) ──────────────────────────┐
│                                                                                │
│  collect-market   collect-policy   collect-youtube   validate-demand           │
│  (매시:17 KST)     (2시간:37)        (하루 4회 :23)      (매일 06:47 KST)           │
│        │               │                │                  │                   │
│        └───────────────┴───────┬────────┴──────────────────┘                   │
│                                ↓                                               │
│                    workers/collectors/*  (Python 3.12)                         │
│                                │                                               │
│  score-and-rank (매시:32) → workers/discovery → workers/scoring                 │
│  daily-report   (07:13)  → TODAY ranking snapshot                              │
└────────────────────────────────┼───────────────────────────────────────────────┘
                                 ↓  (supabase-py, SERVICE_ROLE)
                    ┌────────────────────────┐
                    │  Supabase PostgreSQL   │←── Supabase Cron watchdog (15분)
                    │  (단일 진실 저장소)        │      → system_health / alerts
                    └────────────────────────┘
                                 ↑  (anon key + RLS 읽기 전용)
                    ┌────────────────────────┐
                    │  apps/dashboard        │   TODAY / DETAIL / RADAR /
                    │  (Next.js + Tailwind)  │   SOURCES·HEALTH / WORKFLOWS
                    └────────────────────────┘
                                 │  [글 만들기] → Brief → 경험답변 → Draft
                                 ↓
                    workers/content  (Anthropic API — 유일한 Claude 사용 지점*)
                    (* discovery의 클러스터링 보조 포함)

각 워크플로 시작/성공/실패 → Healthchecks.io ping (GitHub와 독립된 감시자)
```

## 3. 데이터 흐름 (파이프라인 단계별 소유자)

| 단계 | 소유 모듈 | 결정 방식 | 산출 테이블 |
| --- | --- | --- | --- |
| 수집 | `workers/collectors` | 외부 API 호출 | `source_items`, `source_runs` |
| 정규화/중복제거 | `workers/discovery` | deterministic (`content_hash`) | `source_items` |
| Term 추출 | `workers/discovery` | Kiwi tokenizer, 1~4 gram | `terms`, `term_mentions` |
| Event 클러스터링 | `workers/discovery` | 규칙 기반 1차 + Claude 보조(선택적, 실패 시 규칙 기반만) | `candidates` |
| Baseline/Velocity/Acceleration/Novelty | `workers/scoring` | 순수 함수 | `candidate_metrics` |
| Early Signal / Opportunity / Confidence / Rank | `workers/scoring` | 순수 함수, 가중치는 `config/` 상수 + `score_version` | `score_snapshots` |
| Freshness Gate | `workers/scoring` | deterministic ([DATA_FRESHNESS.md](DATA_FRESHNESS.md) §6) | `score_snapshots.freshness_pass` |
| Demand 검증 | `workers/collectors` (daily) | Naver DataLab / Search Ads | `candidate_metrics` |
| Brief / 질문 / Draft | `workers/content` | Claude + deterministic facts 주입 | `content_briefs`, `experience_answers`, `drafts` |
| Monetization Router | `workers/scoring` (점수) + 사용자 (URL 입력) | intent 점수는 deterministic 신호 우선 | `monetization` |

## 4. 모듈 경계 규칙

- `workers/collectors`: 외부 API ↔ raw 저장만. 점수·해석 금지. 소스별 1 파일
  (`naver_search.py`, `naver_datalab.py`, `naver_searchad.py`, `google_trends.py`,
  `youtube.py`, `policy_briefing.py`, `law.py`, `lawmaking_notice.py`, `html_adapter.py`).
  모든 collector는 공통 인터페이스: `collect(source_config, run_ctx) -> SourceRunResult`
  (rows_received, rows_new, source_data_through, error 반환).
- `workers/discovery`: `source_items`만 입력으로 받는다. 외부 API 직접 호출 금지
  (Claude 클러스터링 보조 제외).
- `workers/scoring`: **네트워크 호출 전면 금지.** DB 읽기 → 계산 → DB 쓰기.
  모든 공식은 unit test 대상이며 `score_version`으로 버전 고정.
- `workers/content`: Claude를 호출하는 유일한 모듈. 입력 프롬프트에는 반드시
  deterministic facts 블록이 주입되고, Claude는 그 밖의 수치를 생성할 수 없다
  (출력에서 근거 없는 수치 발견 시 재생성 또는 실패 처리).
- `apps/dashboard`: **읽기 전용.** Supabase anon key + RLS로 read-only view만 접근.
  쓰기는 사용자 피드백(GOOD/BAD/MISSED/WROTE), 경험답변, Shopping Connect URL 입력,
  Brief/Draft 생성 요청뿐이며 이는 별도 허용 정책으로 처리 ([SECURITY.md](SECURITY.md)).
- `packages/shared`: enum·상수의 단일 정의처 (lifecycle, source_type, health status,
  evidence_type, monetization route). TS와 Python 양쪽에서 쓰는 값은 JSON으로 정의하고
  양쪽에서 로드한다 (`packages/shared/constants.json`).

## 5. 화면 구성 (apps/dashboard)

| 경로 | 화면 | 핵심 데이터 |
| --- | --- | --- |
| `/` | TODAY | 헤더(§62), NOW/WATCH 카드, Trust Panel, Fail-Closed 배너 |
| `/candidate/[id]` | Candidate Detail | 차트, evidence drill-down, score breakdown, risk |
| `/radar` | RADAR | lifecycle·category 필터 목록 |
| `/health` | SOURCES / HEALTH | 소스별 Status/Last Success/Data Through/Rows/Error |
| `/workflows` | WORKFLOWS | 워크플로별 최근 20회 실행, Run Now(dispatch 링크) |
| `/write/[briefId]` | Brief → 경험질문 → Draft → NAVER READY export | |

모든 화면 상단에 Dashboard Header(명세 §62) 고정. 데이터는
`v_system_health`, `v_data_cutoff` view에서 온다 ([DATABASE.md](DATABASE.md) §5).

## 6. 배포 형태

- **dashboard**: Next.js, **Vercel 배포 (확정)**. GitHub Pages 정적 export는 사용하지
  않는다. 인증은 **Supabase Auth — 지정된 관리자 이메일 계정만 로그인 가능** (확정).
  URL만 아는 사람에게 공개되는 배포는 금지. anon key는 프론트에서 사용 가능하되 모든
  데이터 접근은 RLS로 보호하고, service_role key는 절대 프론트에 노출하지 않는다.
- **workers**: 배포 없음. GitHub Actions runner에서 `pip install` 후 직접 실행.
  단일 진입점: `python -m workers.run <workflow-name> [--test-mode]`.
- **supabase**: migration은 `supabase/migrations`에 SQL로 보관, `supabase db push`
  또는 CI에서 적용. 재현 가능성이 완료조건 (명세 §65).

## 7. 설정 파일 (config/)

| 파일 | 내용 |
| --- | --- |
| `sources.yaml` | Source Registry (명세 §6 필드 + published_precision) — 워커가 DB에 sync |
| `seeds.yaml` | root keyword (**40~50개로 시작, 확정**) + category(14종)·priority. 코드 hardcode 금지 |
| `scoring.v1.json` | 모든 가중치·임계값 (§26~§36). `score_version` = 이 파일의 버전 |
| `healthchecks.yml` | 워크플로 slug ↔ check 매핑, Period/Grace 정의 |
| `categories.json` | RADAR category, Naver 쇼핑 CID 매핑 |

가중치를 코드에 하드코딩하지 않는 이유: `score_snapshots.components`에 어떤 버전의
어떤 가중치로 계산됐는지 재현 가능하게 남기기 위해서다 (Ranking reproducible, 명세 §65).

## 8. Implementation Order (문서 승인 후)

1. `supabase/migrations` 001~ (스키마 + seed + RLS + view) → 로컬 재현 확인
2. `workers/collectors/naver_search.py` + 공통 run 프레임(workflow_runs/source_runs/heartbeat)
3. `collect-market.yml` — 실제 cron 가동, Healthchecks 연결 → **24시간 방치 테스트**
4. Supabase Cron watchdog + `/health` 화면 (여기까지가 "감시 가능한 뼈대")
5. 나머지 collectors (Google Trends → policy → YouTube → demand)
6. discovery (term extraction, dedupe, candidate rules A~D)
7. scoring + freshness gate + `score-and-rank.yml` + `daily-report.yml`
8. TODAY / Detail / RADAR / WORKFLOWS 화면
9. content (Brief → 질문 → Draft → NAVER READY)
10. Failure Tests A~E (명세 §61) + [ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md) 전체 수행

감시 인프라(1~4)를 collectors 확장(5)보다 먼저 만드는 것이 이 프로젝트의 순서 원칙이다.
"돌았는지 확인할 수 없는 수집기"를 늘리지 않는다.
