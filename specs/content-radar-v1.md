# Content Radar V1 제품 명세서

## 0. 제품 정의

Content Radar는 단순 키워드 검색기가 아니다.

목표는 다음 질문에 매일 답하는 것이다.

> "지금 시각까지 수집된 신뢰 가능한 데이터를 기준으로, 지금 네이버 블로그에 작성할 가치가
> 가장 높은 주제는 무엇이며, 왜 그런가?"

그리고 선택된 주제를 다음 단계까지 연결한다.

```
Signal 발견 → 키워드 생성 → 수요 검증 → 경쟁 검증 → 수익화 판단
→ 콘텐츠 Brief → 사용자 경험 입력 → 네이버 블로그 Draft 생성
```

V1의 최우선 목적은 **네이버 블로그 콘텐츠 생성**이다.

AdSense용 웹사이트 콘텐츠와 Shopping Connect는 데이터 구조와 Monetization Router까지
V1에 포함하지만, 네이버 글쓰기 파이프라인 안정화가 우선이다.

## 1. V1 성공 기준

V1은 다음 조건을 모두 만족해야 성공으로 간주한다.

### 1.1 매일 결과

매일 다음 결과를 제공한다.

- 유효 후보: 5~15개
- 강한 콘텐츠 후보: 3~5개
- TODAY 추천: 1~3개

절대 "키워드 100개"를 성공 KPI로 사용하지 않는다.

### 1.2 추천 신뢰성

TODAY #1 후보는 반드시 다음을 보여야 한다.

- 언제 계산되었는가
- 데이터 기준시각
- 사용한 데이터 소스
- 소스별 마지막 정상 수집시각
- 소스 원문의 게시시각
- 과거 대비 언급량
- 증가속도
- 경쟁 콘텐츠 증가량
- 네이버 검색추세
- 가능한 경우 월 검색량
- 추천 이유
- 추천하지 않을 이유
- 신뢰도

사용자는 #1 추천을 클릭해서 추천 근거가 된 실제 원문까지 확인할 수 있어야 한다.

## 2. 가장 중요한 제품 원칙

### 2.1 AI가 점수를 결정하지 않는다

Claude가 다음을 해서는 안 된다.

- Trend Score 임의 결정
- 검색량 추측
- 증가율 추측
- 경쟁도 추측
- 최신성 추측

숫자 계산은 모두 Python deterministic logic으로 한다.

Claude는 다음에만 사용한다.

- 같은 사건/표현 클러스터링
- 검색의도 추정
- 키워드 의미 분류
- 콘텐츠 적합도 보조판단
- 질문 생성
- Content Brief
- Draft 작성

## 3. Fail-Closed 원칙

시스템이 최신 데이터를 확신할 수 없으면: **추천을 하지 않는다.**

예:

Naver Search collector 실패
→ 기존 데이터로 순위 계산 가능
→ 그러나 TODAY 화면에는 `추천 일시 중지 - Naver Search 데이터가 3시간 12분 동안 갱신되지 않음` 표시
→ #1 추천 노출 금지

절대 오래된 데이터를 최신 데이터처럼 보여서는 안 된다.

## 4. 기술 스택

### Frontend

- Next.js
- TypeScript
- Tailwind CSS
- 단순하고 데이터 중심 UI

구현 시점의 최신 Stable 버전을 확인하여 사용하고 package lock에 고정한다.

### Backend DB

Supabase PostgreSQL.

SQLite를 Production DB로 사용하지 않는다.

이유: GitHub-hosted Action runner는 영구 저장소가 아니므로 실행 간 SQLite persistence를
신뢰할 수 없다.

### Data Worker

Python 3.12+

주요 역할:

- API 수집
- normalize
- deduplicate
- 통계
- scoring
- health check

### Scheduling

GitHub Actions.

### Independent Watchdog

- Healthchecks.io
- Supabase Cron health checker

GitHub Actions가 자기 자신의 실패를 감지하는 유일한 시스템이 되어서는 안 된다.

## 5. 전체 Architecture

```text
Official APIs / RSS / Search APIs
             │
             ↓
       Python Collectors
             │
             ↓
       RAW DATA STORE
        (Supabase)
             │
             ↓
 Normalize / Deduplicate
             │
             ↓
       Term Extraction
             │
             ↓
       Event Clustering
             │
             ↓
  Velocity / Acceleration
             │
             ↓
    Early Signal Score
             │
             ↓
     Demand Validation
             │
             ↓
 Content Opportunity Score
             │
             ↓
      Freshness Gate
             │
       ┌─────┴─────┐
      PASS        FAIL
       │            │
       ↓            ↓
 TODAY Ranking   Ranking Stop
       │
       ↓
 Content Brief
       │
       ↓
 Experience Questions
       │
       ↓
 NAVER Draft
       │
       ↓
 Shopping / Ads routing
```

## 6. 데이터 Source Registry

모든 Source는 DB에서 관리한다.

각 Source는 다음 정보를 가진다.

- `source_id`
- `name`
- `source_type`
- `provider`
- `endpoint_type`
- `collection_interval`
- `expected_data_lag`
- `freshness_sla_minutes`
- `required_for`
- `enabled`
- `priority`
- `official_source` boolean
- `parser_version`

## 7. V1 데이터 소스

### 7.1 Naver Search

NAVER API HUB 기준으로 구현한다.

사용 범위:

- News
- Blog
- Cafe

목적:

- 신규 콘텐츠 감지
- 언급량 계산
- 콘텐츠 공급 증가
- 검색결과 경쟁도 Proxy

Root keyword 기반으로 조회한다. `sort=date`를 우선 사용한다.

### 7.2 Naver Search Trend

목적: 검색 관심도 검증.

주의: **절대 검색량이 아니다. 상대 지수이다.**

반드시 DB에 다음을 따로 저장한다.

- `retrieved_at`
- `period_start`
- `period_end`
- `latest_data_date`
- `ratio`

`retrieved_at`를 데이터 날짜로 사용하면 안 된다.

### 7.3 Naver Shopping Insight

목적: 쇼핑 검색 클릭 관심도 검증.

Shopping Search API의 대체재로 취급하지 않는다. 상품 목록을 제공하는 API가 아니다.

## 8. 매우 중요한 Commerce 제약

2026-08-21 기준 Naver Shopping Search API가 종료되었으므로:

**V1에서는 쇼핑커넥트 상품을 API로 자동 선택하지 않는다.**

시스템이 수행할 것:

- 상품 의도 탐지
- 관련 상품 category 제안
- 글에서 링크 삽입 위치 추천
- CTA 생성
- 쇼핑커넥트 적합도 점수

사용자가 수행할 것:

- Naver Brand Connect에서 실제 Shopping Connect 대상 상품 확인
- 링크 발급
- Content Radar에 URL 입력

시스템은 입력된 URL을 Draft에 삽입한다.

## 9. Naver Search Ads Keyword Tool

목적: 상위 후보의 절대 월간 검색수요 검증.

모든 candidate를 조회하지 않는다. **하루 Top 20~30개만 조회한다.**

저장:

- `monthly_pc_search`
- `monthly_mobile_search`
- `monthly_total_search`
- `related_keywords`
- `fetched_at`

이 수치는 실시간 지표가 아니라 Demand Base 지표로 분류한다.

## 10. Google Trends Trending Now

대한민국 기준. RSS 기반 수집을 우선한다.

목적: 급격한 외부 관심 증가 확인.

저장:

- `title`
- `started_at`
- `updated_at`
- `active_status`
- `traffic_bucket`
- `growth_percent`
- `related_queries`

Google Trending Now는 뉴스성 트렌드에 편향될 수 있으므로 단독으로 높은 점수를 만들지 않는다.

## 11. YouTube

YouTube Data API 사용.

V1에서는 Search quota 때문에 전체 root를 매시간 조회하지 않는다.

최대 20개 High Priority Root를 선정한다.

하루 4회 (KST):

- 02:23
- 08:23
- 14:23
- 20:23

20 queries × 4 = 최대 80 search calls/day. **100 calls/day 이하 유지.**

`publishedAfter`를 사용하여 신규 영상 중심으로 수집한다.

## 12. Policy Signal Sources

### Core

**정책브리핑**

- 정책뉴스
- 보도자료
- 전문자료

**법제처 LAW OPEN DATA**

- 신규/개정 법령
- 시행일
- 공포일
- 법령 변경

**정부입법 / 행정예고**

- 입법예고
- 행정예고
- 예고기간
- 소관부처

## 13. Government / Party Source 확장 구조

HTML Source Adapter를 만든다.

단, 다음 규칙을 만족해야 enable 가능하다.

- 공식 사이트일 것
- robots/이용조건 검토
- 로그인 우회 금지
- CAPTCHA 우회 금지
- 과도한 request 금지
- 기본 fetch interval 2시간 이상
- ETag/Last-Modified 지원 시 사용
- 변경 없으면 parsing 생략

Source Registry에 URL과 parser config를 추가해 확장 가능해야 한다.

정당 자료는 반드시 다음과 같이 유형을 분리한다.

- `PARTY_PROPOSAL`
- `PARTY_PLEDGE`
- `PARTY_POSITION`
- `BILL_PROPOSED`
- `GOVERNMENT_ANNOUNCEMENT`
- `LEGISLATIVE_NOTICE`
- `ENACTED`
- `EFFECTIVE`

정당 발표를 확정된 정부정책으로 표시해서는 안 된다.

## 14. GitHub Actions 구성

### Workflow A — `collect-market.yml`

실행: 매시간 17분 (Asia/Seoul)

목적:

- Google Trends
- Naver News
- Naver Blog
- Naver Cafe

GitHub Actions 부하가 높은 정각 실행을 피한다.

### Workflow B — `collect-policy.yml`

실행: 2시간마다 37분

목적:

- 정책브리핑
- 법령
- 입법예고
- 행정예고
- 등록된 공식 정책 source

### Workflow C — `collect-youtube.yml`

하루 4회 23분.

### Workflow D — `validate-demand.yml`

매일 06:47 KST

목적:

- Naver Search Trend
- Shopping Insight
- Search Ads Keyword Tool

### Workflow E — `score-and-rank.yml`

매시간 32분. Market Collection 이후 실행.

### Workflow F — `daily-report.yml`

매일 07:13 KST. TODAY Ranking 생성.

## 15. 모든 GitHub Workflow 필수사항

모든 workflow에는 반드시 `workflow_dispatch`를 추가한다.
사용자가 GitHub에서 수동 실행 가능해야 한다.

각 workflow는 다음을 DB에 기록한다.

- `run_id`
- `workflow_name`
- `github_run_id`
- `github_sha`
- `trigger_type`
- `scheduled_at`
- `started_at`
- `completed_at`
- `duration_seconds`
- `status`
- `error_message`

## 16. GitHub Action Reliability

GitHub의 cron 실행 여부를 DB만으로 신뢰하지 않는다.

- 각 workflow 시작: Healthchecks.io `/start` ping
- 각 workflow 성공: Success ping
- 실패: Failure ping

Healthchecks.io에서 Period와 Grace Time을 실제 실행주기에 맞게 설정한다.

예 — Hourly collector: Period 1 hour, Grace 30 min.

## 17. Supabase 독립 Watchdog

Supabase Cron은 15분마다 실행한다. GitHub Action과 별개다.

검사:

```text
collect_market last_success > 120 minutes
→ RED

collect_policy last_success > 240 minutes
→ RED

score last_success > 120 minutes
→ RED
```

결과는 `system_health` 테이블에 기록한다.

## 18. 가장 중요한 시간 개념

데이터에는 반드시 서로 다른 세 개의 시간이 존재한다.

| 필드 | 의미 |
| --- | --- |
| `fetched_at` | 우리 시스템이 데이터를 가져온 시간 |
| `published_at` | 원문이 게시된 시간 |
| `source_data_through` | 해당 API가 제공하고 있는 실제 최신 데이터 시점 |

세 값을 절대 혼용하지 않는다.

## 19. Global Data Cutoff

TODAY 화면 상단에 **데이터 완전성 기준시각**을 표시한다.

예:

```text
현재: 2026-08-21 20:55 KST

Market data complete through:
2026-08-21 20:17 KST

Policy data complete through:
2026-08-21 18:37 KST

Naver Search Trend:
2026-08-20

Search Ads Monthly Data:
2026-07
```

따라서 사용자는 데이터의 실제 시차를 이해할 수 있다.

## 20. Freshness SLA

### Real-time class

Naver Search:

- Green < 90m
- Yellow 90~180m
- Red > 180m

Google Trends:

- Green < 90m
- Yellow < 180m
- Red > 180m

### Policy

- Green < 3h
- Yellow 3~6h
- Red > 6h

### YouTube

- Green < 8h
- Yellow 8~16h
- Red > 16h

### Daily validation

Search Trend: API가 반환하는 `latest_data_date` 기준.
Fetch 시간만 보고 Green 처리하지 않는다.

## 21. Candidate Freshness Gate

Candidate마다 필요한 source가 다르다.

### Market Candidate

필수: Naver Search = GREEN

그리고 다음 중 하나: Google Trends = GREEN **OR** YouTube = GREEN

### Policy Candidate

필수:

- Official Policy Source = GREEN
- Naver Search = GREEN

### Evergreen Candidate

필수:

- Search Ads valid
- Search Trend valid
- Naver supply data valid

필수 Source 중 하나라도 RED면 `VERIFIED` 상태를 받을 수 없다.

## 22. Raw Data

원문 수집결과는 삭제하지 않는다. **최소 90일 보관.**

필드:

- `source_item_id`
- `source_id`
- `external_id`
- `canonical_url`
- `title`
- `body_excerpt`
- `author`
- `published_at`
- `fetched_at`
- `raw_payload` JSONB
- `content_hash`
- `language`
- `source_type`

`content_hash`로 duplicate를 방지한다.

## 23. Term Extraction

Python 기반 1차 처리.

한국어: Kiwi 또는 동급의 maintained Korean tokenizer 사용.

추출:

- 명사
- 복합명사
- Product entity
- Organization
- Feature
- Policy term
- Problem expression

1~4 gram까지 후보를 만든다.

## 24. Candidate 생성 최소조건

다음 중 하나를 충족해야 한다.

**Rule A** — 최근 6h distinct documents >= 4 **AND** distinct source types >= 2

**Rule B** — 공식 Policy Event 발생

**Rule C** — Google Trending active **AND** Naver 관련 신규문서 >= 3

**Rule D** — 기존 추적 keyword의 velocity >= 2.0

## 25. Baseline

각 keyword마다 다음을 계산한다.

- last 6h
- previous 6h
- last 24h
- previous 24h
- 7-day same-hour baseline
- 14-day baseline
- 30-day presence

## 26. Velocity

기본:

```text
velocity =
(current_6h + 1)
/
(baseline_6h + 1)
```

단 sparse keyword 폭주 방지를 위해: `distinct_documents < 4`이면 velocity score 제한.

## 27. Acceleration

단순 증가율이 아니라 증가율의 변화 속도를 본다.

```text
velocity_current =
(current_6h + 1) / (prev_6h + 1)

velocity_previous =
(prev_6h + 1) / (prev_prev_6h + 1)

acceleration =
velocity_current / velocity_previous
```

극단값은 percentile cap 적용.

## 28. Novelty

- Novelty 100: 최근 30일 거의 등장하지 않았는데 처음 나타난 표현.
- Novelty 0: 상시 검색되는 기존 표현.

Novelty는 다음으로 deterministic 계산한다.

- `first_seen_at`
- `mentions_30d`
- `document_count_30d`

## 29. Cross Source Score

| Sources | Score |
| --- | --- |
| 1 source | 20 |
| 2 source | 50 |
| 3 source | 75 |
| 4 source | 90 |
| 5+ | 100 |

단 동일 언론사 syndicated article은 하나의 source cluster로 취급한다.

## 30. Early Signal Score

```text
Velocity        25
Acceleration    20
Novelty         20
Cross Source    15
Google Trend    10
Event Freshness 10
```

총 100.

## 31. Search Demand Score

Search Ads 월검색량은 logarithmic normalize한다.

단 검색량 0이라고 해서 Trend candidate를 제거하지 않는다.
신규 키워드는 월 검색량이 존재하지 않을 수 있다.

## 32. Content Supply Gap

단순 검색 결과 `total`만 사용하지 않는다. 최근 신규 콘텐츠를 본다.

예:

- last 24h blog docs
- last 7d blog docs
- last 24h cafe docs
- last 7d ratio

Demand 대비 신규 콘텐츠 공급이 낮을수록 높은 점수를 준다.

## 33. Content Opportunity Score

```text
Early Signal       35
Search Trend       15
Absolute Demand    15
Content Gap        15
Blog Fit           10
Monetization       10
```

총 100.

## 34. Confidence Score

Opportunity와 Confidence를 분리한다.

Confidence 구성:

```text
Source Freshness     35
Evidence Count       25
Cross Source         20
Historical Coverage  10
Official Evidence    10
```

## 35. 최종 Rank Score

```text
rank_score =
opportunity_score
*
(0.70 + 0.30 * confidence_score / 100)
```

**AI가 이 공식을 변경해서는 안 된다.**

## 36. TODAY 노출 조건

### NOW

- Opportunity >= 75
- Confidence >= 70
- Freshness Gate PASS

### WATCH

- Opportunity 55~74
- 또는 Confidence 50~69

### LATE

검색/콘텐츠 공급 증가가 이미 상당히 진행되어 Opportunity 감소.

## 37. 화면 1 — TODAY

상단에 항상:

```text
SYSTEM HEALTH ● GREEN

Last pipeline:
20:32 KST

Market data complete:
20:17 KST

Policy data complete:
18:37 KST

Core sources:
6 / 6 HEALTHY
```

하나라도 문제가 있으면 상단 RED banner.

## 38. TODAY Candidate Card

예:

```text
#1 오즈모포켓4 축구촬영

Opportunity 84
Confidence 88

상태:
NOW

왜 지금인가

+ 최근 6h 언급 3.4배
+ acceleration 2.1
+ Blog/Cafe/YouTube 동시 증가
+ 네이버 검색 추세 상승 시작
+ 최근 7일 콘텐츠 공급 낮음

데이터 기준:
20:17 KST

Evidence:
23 documents

[근거 보기]
[글 만들기]
```

## 39. 화면 2 — Candidate Detail

반드시 다음을 표시한다.

- **Trend**: 6h / 24h / 7d chart
- **Source Breakdown**: News / Blog / Cafe / YouTube / Google / Policy
- **Evidence**: 실제 title, published_at, source, URL
- **Demand**: Search Trend, Search Ads, Shopping Insight
- **Supply**: 최근 Blog/Cafe 게시량
- **Score Breakdown**: 각 score component
- **Risk**: "왜 이 키워드가 실패할 수 있는가?"

## 40. 설명은 숫자로 먼저 만들고 Claude는 나중에 사용한다

추천 이유의 핵심 문구는 deterministic facts로 생성한다.

Claude가 "요즘 인기가 많은 것 같습니다." 같이 근거 없는 문장을 만들지 못하게 한다.

## 41. 화면 3 — RADAR

필터:

- NOW
- WATCH
- RISING
- MATURE
- EXPIRED

Category:

- Product
- Tech
- Life
- Policy
- Finance
- Parenting
- Work
- AI
- etc.

## 42. 화면 4 — SOURCES / HEALTH

**이 화면은 V1 핵심이다.**

표:

| Source | Status | Last Success | Data Through | Rows | Error |
| --- | --- | --- | --- | --- | --- |

사용자가 한눈에 Action 작동 여부를 확인할 수 있어야 한다.

## 43. Workflow 화면

각 workflow 최근 20개 실행:

- Started
- Completed
- Duration
- Status
- Items
- New items
- GitHub run id
- Commit SHA

GitHub Actions 화면을 열지 않아도 된다.

## 44. 수동 검증

모든 Collector에 `Run Now` 기능을 제공한다.

Dashboard button이 어려우면 V1에서는 GitHub `workflow_dispatch` 링크를 제공한다.

## 45. Content Creation

Candidate에서 `글 만들기` 클릭.

AI가 곧바로 글을 쓰지 않는다. 먼저 Content Brief 생성.

## 46. Content Brief

다음 구조:

- Target keyword
- Supporting keywords
- Search intent
- Reader problem
- Why now
- Must-answer questions
- Facts to verify
- Experience needed
- Suggested images
- Monetization opportunity

## 47. 사용자 경험 질문

상품/사용경험 콘텐츠라면 Claude가 최대 7개 질문 생성. 답변 저장.

Draft는 이 답변을 최우선 Primary Source로 활용한다.

**경험이 없는 내용을 경험했다고 작성해서는 안 된다.**

## 48. Policy Content

Policy 글에서는 개인 경험을 강제하지 않는다.

대신 반드시 다음을 Content Brief에 넣는다.

- 공식 발표일
- 정책 상태
- 시행일
- 적용대상
- 확정/예고 여부
- 공식 source

입법예고를 "시행 확정"이라고 작성해서는 안 된다.

## 49. NAVER Draft

결과:

- 제목 후보 3개
- 추천 제목 1개
- 본문
- 이미지 삽입 위치
- FAQ
- 태그
- 출처
- Shopping Connect disclosure
- 링크 삽입 위치

## 50. Naver 자동발행 제외

V1에서는 네이버 블로그 자동 로그인/자동 게시를 구현하지 않는다.

최종 `NAVER READY` 상태에서 사용자가:

- 본문 복사
- 사진 추가
- Shopping Connect 링크 확인
- 최종 검토
- 발행

한다.

## 51. Monetization Router

각 Candidate를 다음으로 분류한다.

| Route | 의미 |
| --- | --- |
| `NAVER_SHOPPING` | 구매의도 높음 |
| `NAVER_TRAFFIC` | 정보성 검색 |
| `WEB_ADSENSE` | 장기 Evergreen 검색 가능 |
| `DUAL` | 네이버 + 별도 웹 콘텐츠 모두 가치 있음 |

## 52. Shopping Connect Score

다음 기반 (100점):

- product intent
- comparison intent
- review intent
- price intent
- accessory intent
- problem-solving intent

단 실제 Shopping Connect 상품 존재 여부는 사용자가 확인한다.

## 53. AdSense Score

다음 기반:

- evergreen longevity
- informational depth
- repeat search
- question diversity
- calculator/tool potential
- long-tail expansion

V1에서는 점수와 콘텐츠 기획까지만 구현한다.
공개 AdSense 사이트 자동발행은 다음 Release로 분리한다.

## 54. DB Tables

### sources

- `source_id` UUID PK
- `name`
- `provider`
- `source_type`
- `freshness_sla_minutes`
- `collection_interval`
- `enabled`
- `required_for` JSONB
- `config` JSONB
- `created_at`

### workflow_runs

- `id` UUID
- `workflow_name`
- `github_run_id`
- `github_sha`
- `trigger`
- `scheduled_at`
- `started_at`
- `completed_at`
- `status`
- `error`
- `items_received`
- `items_new`

### source_runs

- `id`
- `source_id`
- `workflow_run_id`
- `started_at`
- `completed_at`
- `status`
- `http_status`
- `rows_received`
- `rows_new`
- `source_data_through`
- `error`

### source_items

- `id`
- `source_id`
- `external_id`
- `url`
- `title`
- `excerpt`
- `published_at`
- `fetched_at`
- `raw_payload`
- `content_hash`
- UNIQUE (`source_id`, `external_id`)

### terms

- `id`
- `normalized_term`
- `display_term`
- `first_seen_at`
- `last_seen_at`
- `category`

### term_mentions

- `term_id`
- `source_item_id`
- `published_at`
- `source_type`

### candidates

- `id`
- `primary_term_id`
- `cluster_name`
- `lifecycle`
- `category`
- `created_at`
- `updated_at`

### candidate_metrics

- `candidate_id`
- `window_start`
- `window_end`
- `mentions`
- `distinct_documents`
- `distinct_sources`
- `velocity`
- `acceleration`
- `novelty`
- `search_trend`
- `monthly_search`
- `content_supply`

### score_snapshots

- `candidate_id`
- `score_version`
- `calculated_at`
- `data_complete_through`
- `early_signal`
- `opportunity`
- `confidence`
- `rank_score`
- `freshness_pass`
- `components` JSONB

**절대 기존 snapshot overwrite 금지.**

### candidate_evidence

- `candidate_id`
- `source_item_id`
- `evidence_type`
- `weight`

### content_briefs

- `candidate_id`
- `version`
- `brief_json`
- `created_at`

### experience_answers

- `brief_id`
- `question`
- `answer`

### drafts

- `brief_id`
- `destination`
- `version`
- `title`
- `body`
- `status`
- `created_at`

### monetization

- `candidate_id`
- `shopping_score`
- `adsense_score`
- `route`
- `shopping_connect_url`

### system_health

- `component`
- `status`
- `last_success_at`
- `data_through`
- `checked_at`
- `message`

### alerts

- `severity`
- `component`
- `message`
- `created_at`
- `resolved_at`

## 55. Historical Audit

**Score는 절대 덮어쓰지 않는다.**

예:

```text
2026-08-21 12:32  XRP ETF  Opportunity 61
2026-08-21 18:32  XRP ETF  Opportunity 79
```

같이 변화 과정을 확인 가능해야 한다.

## 56. Prediction Log

Candidate가 NOW가 된 최초 시점을 기록한다.

추후 다음과 연결한다.

- 실제 검색량 상승
- 언급량 상승
- 콘텐츠 조회
- 수익

## 57. Precision 측정

매월 Precision@3, Precision@5를 계산할 수 있는 구조로 만든다.

사용자가 Candidate에 다음 feedback을 줄 수 있다.

- GOOD
- BAD
- MISSED
- WROTE

## 58. GitHub Actions Workflow 공통 설정

- `timeout-minutes` 필수
- `concurrency` 설정
- secrets 사용
- retry with exponential backoff
- request timeout
- structured logging
- 실패 시 DB run status 기록
- Healthchecks failure ping
- `workflow_dispatch`
- test mode

## 59. Secrets

GitHub Secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `NAVER_API_CLIENT_ID`
- `NAVER_API_CLIENT_SECRET`
- `NAVER_SEARCHAD_*`
- `YOUTUBE_API_KEY`
- `DATA_GO_KR_SERVICE_KEY`
- `LAW_API_KEY`
- `ANTHROPIC_API_KEY`
- `HEALTHCHECKS_*`

Repository에 secret value가 commit되어서는 안 된다.

## 60. 테스트

### Unit

- scoring
- normalization
- dedupe
- freshness
- lifecycle

### Contract Test

각 외부 API response fixture를 저장. Parser가 예상 schema를 읽는지 검사.

### Integration

Mock API → DB → score → dashboard.

## 61. 가장 중요한 Failure Tests

반드시 실제로 테스트한다.

**Test A** — Naver API 강제 실패.
예상: Source RED. TODAY VERIFIED recommendation 금지.

**Test B** — GitHub workflow 실행하지 않음.
예상: Heartbeat overdue. Healthchecks alert. Dashboard RED.

**Test C** — Search Trend API는 성공했지만 `latest_data_date`가 오래됨.
예상: `fetched_at`은 최신이어도 Trend status는 stale.

**Test D** — 원문 duplicate 10건.
예상: 하나의 사건을 10개 독립 source로 세지 않음.

**Test E** — Claude API 실패.
예상: Score와 Radar는 정상동작. Draft만 실패.
AI 장애가 Radar를 중단시켜서는 안 된다.

## 62. Dashboard Header

모든 화면 상단에 고정.

예:

```text
CONTENT RADAR

SYSTEM ● HEALTHY

현재시각
20:55 KST

최종 Pipeline
20:32

Market complete through
20:17

Policy complete through
18:37

Daily Search Data
D-1

6/6 core sources healthy
```

이 정보가 없으면 V1 완료로 인정하지 않는다.

## 63. #1 Candidate Trust Panel

#1 추천 옆에 `왜 믿어도 되나요?` 버튼.

클릭하면 다음을 표시한다.

- 6/6 required source healthy
- 23 distinct documents
- 4 source types
- Naver 6h velocity 3.4x
- 14d baseline 비교
- Search Trend +18%
- Content Supply low
- scoring version v1.0.0
- calculated 20:32
- data through 20:17

## 64. 절대 금지

- Source timestamp 없는 데이터
- AI 생성 검색량
- AI 생성 경쟁도
- 오래된 데이터인데 Green 표시
- Collector 실패를 무시하고 ranking
- 공식정책과 정당공약 혼동
- 입법예고를 시행 확정으로 표현
- Naver 자동 로그인
- 비공식 Shopping Connect 링크 자동 생성
- CAPTCHA 우회
- 무단 고빈도 scraping
- raw evidence 없는 추천

## 65. V1 Deployment 완료조건

아래가 모두 PASS되어야 Production Ready.

- [ ] Supabase migration 재현 가능
- [ ] GitHub Actions 24시간 연속 성공
- [ ] Healthchecks heartbeat 확인
- [ ] Action 하나 중단 시 alert 발생
- [ ] Dashboard Source Health 작동
- [ ] Data Through 정확하게 표시
- [ ] Raw evidence drill-down 가능
- [ ] 후보 5개 이상 생성
- [ ] Freshness Gate 정상
- [ ] Ranking reproducible
- [ ] Content Brief 생성
- [ ] Experience Questions 생성
- [ ] Naver Draft 생성
- [ ] Shopping Connect URL 수동입력 가능
- [ ] NAVER READY export 가능
- [ ] README 설치 절차 완성
- [ ] 모든 secret `.env.example`만 존재
- [ ] 실제 secret repository 미포함

## 66. V1 이후

### V1.1

- AdSense public site
- Evergreen content generator
- calculator/tool pages

### V1.2

- actual Naver performance ingestion
- Shopping Connect revenue feedback

### V2

- 추천 score의 실적 기반 weight calibration
- Event → future keyword prediction
- category별 scoring model
