# Content Radar

> "지금까지 수집된 신뢰 가능한 데이터를 기준으로, 지금 네이버 블로그에 작성할 가치가
> 가장 높은 주제는 무엇이며, 왜 그런가?"

Content Radar는 이 질문에 매일 답하는 시스템이다. 단순 키워드 검색기가 아니다.

```
Signal 발견 → 키워드 생성 → 수요 검증 → 경쟁 검증 → 수익화 판단
→ 콘텐츠 Brief → 사용자 경험 입력 → 네이버 블로그 Draft 생성
```

## 핵심 원칙

1. **AI가 점수를 결정하지 않는다.** 모든 숫자(velocity, novelty, score)는 Python
   deterministic logic으로 계산한다. Claude는 클러스터링·의도분류·Brief·Draft에만 쓴다.
2. **Fail-Closed.** 최신 데이터를 확신할 수 없으면 추천하지 않는다. 오래된 데이터를
   최신처럼 보여주지 않는다.
3. **세 개의 시간을 혼용하지 않는다.** `fetched_at`(수집시각) / `published_at`(원문
   게시시각) / `source_data_through`(API의 실제 데이터 최신 시점)는 항상 분리 저장한다.
4. **대시보드만 보고 검증 가능해야 한다.** "GitHub Actions가 실제로 돌았는가",
   "추천이 몇 시 데이터 기준인가"를 GitHub 화면 없이 확인할 수 있어야 한다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Frontend | Next.js + TypeScript + Tailwind CSS |
| Database | Supabase PostgreSQL |
| Data Worker | Python 3.12+ |
| Scheduling | GitHub Actions (cron) |
| Watchdog | Healthchecks.io + Supabase Cron (GitHub Actions와 독립) |
| AI | Anthropic API (Claude) |

## 저장소 구조

```
content-radar/
  apps/
    dashboard/        # Next.js 대시보드 (TODAY / Detail / RADAR / SOURCES / WORKFLOWS)
  workers/
    collectors/       # 소스별 수집기 (Naver, Google Trends, YouTube, 정책)
    discovery/        # normalize, dedupe, term extraction, clustering, candidate 생성
    scoring/          # baseline, velocity, acceleration, novelty, scores, freshness gate
    content/          # Content Brief, 경험 질문, NAVER Draft (Claude 사용 영역)
  packages/
    shared/           # TS/Python 공용 상수·타입 (lifecycle, status enum 등)
  supabase/
    migrations/       # DB migration (재현 가능해야 함)
  config/             # Source Registry seed, root keywords, healthchecks 매핑
  fixtures/           # 외부 API response fixture (contract test 용)
  tests/              # unit / contract / integration / failure tests
  docs/               # 설계 문서 (아래 참조)
  specs/              # 제품 명세서
  .github/workflows/  # collect-market, collect-policy, collect-youtube,
                      # validate-demand, score-and-rank, daily-report, watchdog
```

## 문서

구현 전 반드시 읽어야 하는 순서:

| 문서 | 내용 |
| --- | --- |
| [specs/content-radar-v1.md](specs/content-radar-v1.md) | **V1 제품 명세서 (원본, 최우선 기준)** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 전체 구조, 데이터 흐름, 컴포넌트 경계 |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | 외부 API별 검증된 스펙 (2026-08 기준) |
| [docs/DATA_FRESHNESS.md](docs/DATA_FRESHNESS.md) | 시간 모델, Freshness SLA, Gate, Data Cutoff 계산 |
| [docs/DATABASE.md](docs/DATABASE.md) | 스키마, 제약, snapshot 불변 원칙 |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | GitHub Actions 설계, run 기록, heartbeat |
| [docs/FAILURE_MODES.md](docs/FAILURE_MODES.md) | 장애 시나리오별 기대 동작 |
| [docs/SECURITY.md](docs/SECURITY.md) | Secrets, RLS, 수집 윤리 |
| [docs/SETUP_REQUIRED.md](docs/SETUP_REQUIRED.md) | 사용자가 직접 해야 하는 계정/키 발급 절차 |
| [docs/ACCEPTANCE_TESTS.md](docs/ACCEPTANCE_TESTS.md) | V1 완료조건 검증 절차 |

## 설치 (요약)

> 상세 절차는 [docs/SETUP_REQUIRED.md](docs/SETUP_REQUIRED.md) 참조.

1. Supabase 프로젝트 생성 → `supabase/migrations` 적용
2. 외부 API 키 발급 (Naver Open API, Naver Search Ads, YouTube, data.go.kr, 법제처, Anthropic)
3. Healthchecks.io 프로젝트 생성, 워크플로별 check 등록
4. `.env.example` → GitHub Secrets 등록
5. GitHub Actions 활성화 후 `workflow_dispatch`로 첫 수집 실행
6. 대시보드에서 SOURCES / HEALTH 화면 확인

## 상태

**현재 단계: 설계 문서 확정 (코딩 전).** 구현 순서는
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)의 Implementation Order 참조.
