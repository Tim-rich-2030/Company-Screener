# ACCEPTANCE_TESTS

V1을 "Production Ready"로 선언하기 위한 검증 절차. 명세 §60(테스트 계층),
§61(Failure Tests), §65(완료조건)를 실행 가능한 체크 절차로 옮긴 것이다.
**전부 PASS해야 완료다. 일부 PASS는 완료가 아니다.**

## 1. 테스트 계층 (명세 §60)

### 1.1 Unit (CI에서 매 PR)

| 대상 | 필수 케이스 |
| --- | --- |
| velocity/acceleration | 공식 §26~§27 그대로, sparse 제한(distinct_docs<4), 0 나눗셈 없음(+1 스무딩), percentile cap |
| novelty | first_seen 30일 밖/안, mentions_30d 경계 |
| cross source | 1~5+ 소스 매핑표, syndication cluster 1개 취급 |
| opportunity/confidence/rank | 가중치 합=100 검증, rank 공식 §35 고정값 테스트 |
| freshness | `source_status` 경계값(89/90/180/181분, D-1/D-2/D-3), gate 조합 전수, data_through 단조성 |
| normalization | `<b>` 태그 제거, URL 정규화, content_hash 안정성 |
| dedupe | external_id/hash 2단계 |
| lifecycle | new→rising→now→late→expired 전이 규칙 |
| 시간 처리 | UTC↔KST 경계(자정), naive datetime 금지 lint |

### 1.2 Contract (CI에서 매 PR)

- `fixtures/`에 소스별 **실제 응답** 저장 (sanitize 후). 파서가 fixture를 읽어
  기대 스키마(`published_precision` 포함)로 변환되는지 검사.
- **fixture 없는 소스는 Source Registry enable 불가** — CI가 enabled 소스 목록과
  fixture 존재를 대조해 실패시킨다.
- 스키마 변형 fixture(필드 누락, null, `"< 10"` 센티널)도 각 1개 이상.

### 1.3 Integration (CI 또는 로컬)

- Mock API 서버 → collector → Supabase(local) → discovery → scoring → view 조회까지
  1회 관통. TODAY용 view가 기대 후보를 반환하는지 확인.

## 2. Failure Tests (명세 §61 — 실환경에서 실제 수행, 결과 기록)

수행 방법: 아래 각 테스트는 스테이징(또는 초기 운영) 환경에서 **실제로 유발**하고,
결과 스크린샷/기록을 `docs/acceptance/` 에 남긴다.

| ID | 유발 방법 | PASS 조건 |
| --- | --- | --- |
| **A** Naver 강제 실패 | GitHub Secret의 NAVER 키를 임시 오염 → collect-market 2~3회 실행 대기 | /health에서 Naver RED, TODAY RED 배너 + 중지 문구(경과시간 포함), NOW 카드 숨김. 다른 소스 수집은 계속 |
| **B** 워크플로 미실행 | collect-market 워크플로 disable 후 2.5시간 방치 | Healthchecks alert 수신(이메일), system_health RED(watchdog 경유), 대시보드 헤더 RED. **DB에는 기록이 없어도 감지됨** 확인 |
| **C** Search Trend stale | fixture 주입 또는 mock으로 `latest_data_date=D-3` 응답 | fetched_at 최신임에도 Trend 상태 RED, Evergreen gate 불통과 |
| **D** duplicate 10건 | 동일 기사 10 변형 fixture 주입 | distinct_documents가 1~2로 집계, cross_source 점수가 1 소스로 계산 |
| **E** Claude 실패 | ANTHROPIC_API_KEY 오염 후 전체 파이프라인 + [글 만들기] | score/rank/TODAY 정상 생성, Draft만 실패 + 화면 에러 표시 |

추가 (FAILURE_MODES 근거):

| ID | 유발 | PASS 조건 |
| --- | --- | --- |
| F4' silent breakage | items 항상 빈 배열인 mock 3회 | 소스 YELLOW + alert |
| F12' watchdog 정지 | pg_cron job 일시 unschedule 30분 | watchdog-supabase alert 수신 |

## 3. V1 완료조건 체크리스트 (명세 §65 → 검증 방법)

| # | 조건 | 검증 방법 |
| --- | --- | --- |
| 1 | Supabase migration 재현 | 빈 프로젝트에 `supabase db push` → seed까지 1회로 완료, 문서와 diff 없음 |
| 2 | Actions 24시간 연속 성공 | /workflows에서 24h 내 모든 스케줄 run success/partial-원인-소명, 누락 0 |
| 3 | Healthchecks heartbeat | 7개 check 전부 "Up", 실행시간 그래프 존재 (/start 사용 증명) |
| 4 | Action 중단 시 alert | Failure Test B 기록 |
| 5 | Source Health 화면 | /health에서 소스별 Status/Last Success/Data Through/Rows/Error 표시 |
| 6 | Data Through 정확 | 임의 시점에 /health의 data_through와 DB `source_runs` 값 대조 일치 |
| 7 | Raw evidence drill-down | TODAY #1 → 근거 보기 → 실제 원문 title/published_at/URL 클릭 도달 |
| 8 | 후보 5개 이상 | 실데이터 기준 유효 후보 5~15개 생성되는 날 확인 |
| 9 | Freshness Gate | Failure Test A/C 기록 |
| 10 | Ranking reproducible | 임의 snapshot의 `components`+`score_version`으로 재계산 → rank_score 일치 |
| 11 | Content Brief | 실후보에서 §46 구조 전 항목 생성 |
| 12 | Experience Questions | 상품형 후보에서 ≤7개 질문 생성·답변 저장 |
| 13 | Naver Draft | §49 구조(제목 3+1, 본문, 이미지 위치, FAQ, 태그, 출처, disclosure, 링크 위치) 확인 |
| 14 | Shopping Connect URL 수동입력 | 대시보드 입력 → Draft에 삽입, 비 naver.com URL 거부 |
| 15 | NAVER READY export | 본문 복사 가능한 최종 화면 동작 |
| 16 | README 설치 절차 | 제3자(또는 새 환경)가 README+SETUP_REQUIRED만으로 설치 재현 |
| 17 | secret은 .env.example만 | `git log -p` 전체에서 secret 패턴 스캔 0건, GitHub secret scanning 클린 |
| 18 | 실제 secret 미포함 | 위와 동일 + fixture sanitize 확인 |

## 4. 지표 측정 준비 (명세 §56~§57)

완료조건은 아니지만 V1 출고 시점에 준비돼 있어야 하는 것:

- `candidates.first_now_at` 기록 동작 확인 (Prediction Log)
- `feedback` 입력 UI 동작 + Precision@3/@5 계산 쿼리 존재
  (`select ... from score_snapshots join feedback ...` — 월별 실행 가능하면 됨)

## 5. 진행 규칙

- 이 문서의 체크는 PR로 갱신한다 (체크 표시 + 증적 링크). 구두 "확인했음"은 무효.
- Failure Test는 **한 번 통과한 뒤 아키텍처가 바뀌면 다시 수행**한다
  (특히 소스 추가, Gate 규칙 변경 시).
