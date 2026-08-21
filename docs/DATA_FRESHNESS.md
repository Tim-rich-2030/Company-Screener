# DATA_FRESHNESS

이 문서는 Content Radar의 시간·신선도 모델 전체를 정의한다.
이 시스템의 존재 이유가 "**추천 데이터가 실제 몇 시까지 최신인지 대시보드만 보고 검증**"이므로,
여기 정의된 규칙은 구현에서 임의로 바꿀 수 없다. 변경은 이 문서 수정 → 리뷰 → 코드 순서로만 한다.

명세 근거: §3 (Fail-Closed), §18 (세 개의 시간), §19 (Global Data Cutoff), §20 (SLA),
§21 (Freshness Gate), §64 (절대 금지).

---

## 1. 세 개의 시간 — 정의와 불변 규칙

| 필드 | 정의 | 생성 주체 | 예 |
| --- | --- | --- | --- |
| `fetched_at` | 우리 collector가 해당 데이터를 API에서 받아온 순간 | 우리 시스템 (`datetime.now(UTC)`) | 2026-08-21 11:17:42Z |
| `published_at` | 원문이 실제로 게시된 시각 | 원문/API가 제공 | 뉴스 pubDate, 블로그 postdate |
| `source_data_through` | "이 소스에 대해 **이 시각까지의 데이터는 우리가 갖고 있다**"고 보증할 수 있는 시각 | collector가 규칙(§2)으로 계산 | 2026-08-21 11:17Z 또는 2026-08-20 (일 단위) |

불변 규칙:

1. 세 값은 서로 대체 불가. 특히 `fetched_at`을 `source_data_through`로 쓰는 것은
   금지 — API가 성공 응답을 주더라도 그 안의 데이터가 오래됐을 수 있다 (명세 §61 Test C).
2. DB 저장은 전부 **UTC `timestamptz`** (일 단위 값은 `date`). 화면 표시만 KST.
   KST = UTC+9 고정, DST 없음.
3. `published_at`이 없는 데이터는 저장 자체를 거부한다 (명세 §64 "Source timestamp 없는
   데이터" 금지). 예외는 **API가 구조적으로 게시시각을 제공하지 않음이 공식 문서로
   확인된 경우**뿐이며, 그 사실을 `published_precision` 필드로 데이터에 남긴다.
   2026-08 검증 결과 실제로 해상도가 소스마다 다르다: News=초 단위, **Blog=날짜만
   (yyyymmdd)**, **Cafe=게시시각 없음**, 검색광고=월 단위. precision은 4값 enum
   (`SECOND|MINUTE|DAY|UNKNOWN`)으로 저장하며, **`DAY`/`UNKNOWN` 데이터는 6h
   velocity/acceleration 계산에서 제외**하고 24h+ 윈도우·daily supply·cross-source
   evidence에만 쓴다. 정확한 시간 데이터와 날짜 단위 데이터를 같은 정밀도로 계산하는
   것은 금지다. 상세 규칙은 [DATABASE.md](DATABASE.md) §2.
4. `source_data_through`는 **단조 증가**한다. 새 run이 계산한 값이 기존 값보다 과거이면
   기존 값을 유지하고 경고 로그를 남긴다 (API가 일시적으로 빈 응답을 줄 때 후퇴 방지).

---

## 2. 소스별 `source_data_through` 계산 규칙

collector마다 "성공"의 의미가 다르므로, 성공한 run이 `source_runs.source_data_through`에
기록하는 값을 소스별로 고정한다.

| 소스 | 계산 규칙 | 근거 |
| --- | --- | --- |
| Naver Search News/Blog/Cafe | 성공한 run의 `started_at` | `sort=date`로 최신부터 페이지네이션하며 직전 data_through와 겹칠 때까지 수집 → run 시작 시점까지의 신규 문서를 모두 확보했다고 볼 수 있음. 단 Naver 인덱싱 지연은 보증 밖 (§8 한계) |
| Google Trends Trending Now | 성공한 run의 `started_at` | RSS 스냅샷은 조회 시점의 활성 트렌드 목록 |
| YouTube | 성공한 run의 `started_at` | `publishedAfter=직전 data_through`로 조회 |
| 정책브리핑 / 법령 / 입법·행정예고 | 성공한 run의 `started_at` | 목록형 API를 날짜 내림차순으로 신규분 확인 |
| Naver Search Trend (DataLab) | **API 응답의 마지막 데이터 포인트 날짜** (`latest_data_date`) | 응답의 `retrieved_at`은 최신이어도 데이터는 보통 D-1. fetch 시각 사용 금지 (명세 §7.2) |
| Naver Shopping Insight | 응답의 마지막 데이터 포인트 날짜 | 동일 |
| Naver Search Ads Keyword Tool | 데이터의 기준 월 (`YYYY-MM`) — 최근 30일 집계이므로 조회일 기준 월로 기록하되 **Demand Base 지표**로 분류, 실시간 신선도 판정 대상에서 제외 | 명세 §9 |

구현 규칙: 각 collector는 `SourceRunResult.source_data_through`를 **반드시 채운다.**
채우지 못하면 그 run은 성공으로 기록될 수 없다.

---

## 3. 소스 상태 판정 (GREEN / YELLOW / RED)

### 3.1 판정에 쓰는 두 개의 나이

```
collection_age = now - last_success_at        # 수집이 언제 마지막으로 성공했나
data_age       = now - source_data_through    # 확보한 데이터가 실제 얼마나 오래됐나
```

`last_success_at` = 해당 source의 마지막 `status='success'`인 `source_runs.completed_at`.
HTTP 200 + 정상 파싱 + rows_received ≥ 0 이면 성공이다. **rows_received=0은 실패가 아니다**
(신규 문서가 없었을 뿐, data_through는 전진한다).

### 3.2 판정 함수 (deterministic, 소스 SLA는 Source Registry에서 읽음)

```python
def source_status(source, now) -> Status:
    lr = last_success_run(source)
    if lr is None:
        return RED                      # 한 번도 성공한 적 없음

    if source.cadence == "realtime":    # Naver Search, Google Trends, Policy, YouTube
        age_min = minutes(now - lr.completed_at)
        if age_min <  source.sla.green_lt:  return GREEN
        if age_min <  source.sla.red_gte:   return YELLOW
        return RED

    if source.cadence == "daily":       # Search Trend, Shopping Insight
        # 수집 자체가 밀렸는지 + 데이터가 오래됐는지 둘 다 본다
        if minutes(now - lr.completed_at) > 26 * 60:      return RED   # 일배치 누락
        lag_days = (today_kst(now) - lr.source_data_through).days
        if lag_days <= source.sla.expected_lag_days:      return GREEN # 보통 D-1
        if lag_days == source.sla.expected_lag_days + 1:  return YELLOW
        return RED

    if source.cadence == "monthly":     # Search Ads → Demand Base
        # 실시간 신선도 판정 없음. 데이터 기준월만 표시. 35일 초과 미갱신 시 YELLOW.
        return GREEN if days(now - lr.completed_at) <= 35 else YELLOW
```

### 3.3 SLA 값 (Source Registry seed, 명세 §20 그대로)

| 소스 | cadence | GREEN | YELLOW | RED |
| --- | --- | --- | --- | --- |
| Naver Search (News/Blog/Cafe 각각) | realtime | < 90m | 90–180m | > 180m |
| Google Trends | realtime | < 90m | 90–180m | > 180m |
| 정책 소스 (각각) | realtime | < 3h | 3–6h | > 6h |
| YouTube | realtime | < 8h | 8–16h | > 16h |
| Naver Search Trend | daily | data ≤ D-1 | data = D-2 | 그 이상 / 26h 수집누락 |
| Shopping Insight | daily | data ≤ D-1 | data = D-2 | 그 이상 / 26h 수집누락 |
| Search Ads | monthly | 기준월 표시만 | 35일 미갱신 | — |

주의: SLA 숫자는 코드에 하드코딩하지 않는다. `sources.freshness_sla_minutes` /
`config/sources.seed.json`이 유일한 정의처다.

### 3.4 이상 징후 보조 규칙 (상태를 올리진 않고 YELLOW로만 강등)

- 고볼륨 소스(Naver News/Blog)가 **연속 3회 run에서 rows_received=0** → YELLOW + alert.
  "성공했지만 사실은 파서가 깨져 아무것도 못 읽는" 케이스를 잡기 위함.
- `source_data_through`가 24h 이상 전진하지 않는 realtime 소스 → YELLOW + alert.

---

## 4. Global Data Cutoff — "몇 시까지 최신인가"의 단일 계산식

대시보드 헤더(명세 §62)와 TODAY 화면(§19)에 표시되는 값들. **모든 화면이 같은 계산을 쓴다**
— DB view `v_data_cutoff` 하나로 정의하고 프론트는 표시만 한다.

```
market_complete_through = min(
    data_through(naver_news),
    data_through(naver_blog),
    data_through(naver_cafe),
    data_through(google_trends),
)

policy_complete_through = min(data_through(각 enabled 정책 소스))

youtube_complete_through = data_through(youtube)

search_trend_data_date  = source_data_through(naver_search_trend)      # date
shopping_insight_date   = source_data_through(naver_shopping_insight)  # date
searchad_data_month     = source_data_through(naver_searchad)          # YYYY-MM

last_pipeline_at        = 마지막 성공한 score-and-rank run의 completed_at
```

`min()`을 쓰는 이유: "complete through"는 보증이다. 하나라도 뒤처진 소스가 있으면
그 시각까지만 완전하다고 말할 수 있다.

disabled 소스는 계산에서 제외한다. **enabled인데 성공 run이 없는 소스는 cutoff를
`NULL`로 만들고 헤더를 RED로 만든다** — "모르는 것"을 낙관적으로 건너뛰지 않는다.

표시 형식 (헤더 고정):

```
현재:                      2026-08-21 20:55 KST     ← 브라우저 렌더 시각
Market complete through:   2026-08-21 20:17 KST
Policy complete through:   2026-08-21 18:37 KST
Naver Search Trend:        2026-08-20 (D-1)
Search Ads Monthly:        2026-07
Last pipeline:             20:32 KST
```

---

## 5. Score 시점의 신선도 고정 (snapshot 원칙)

`score-and-rank`는 실행 시점에 아래를 계산해 `score_snapshots`에 **함께 저장**한다:

- `data_complete_through` = 그 시점의 `market_complete_through`
  (policy candidate면 policy도, 명세 §21의 필수 소스 기준)
- `freshness_pass` = §6 Gate 결과
- `components.source_status` = 소스별 상태 스냅샷 (JSONB)

이유: "이 추천이 계산될 당시 무슨 데이터가 있었는가"를 나중에 감사할 수 있어야 한다
(명세 §55). 대시보드의 실시간 상태와 snapshot의 상태는 다를 수 있으며, TODAY 노출
여부는 **둘 다** 통과해야 한다 (§7).

---

## 6. Candidate Freshness Gate (명세 §21 → 판정 함수)

```python
def freshness_gate(candidate_type, st: dict[SourceKey, Status]) -> bool:
    ok = lambda s: s == GREEN

    if candidate_type == MARKET:
        naver_ok = all(ok(st[s]) for s in [NAVER_NEWS, NAVER_BLOG, NAVER_CAFE])
        return naver_ok and (ok(st[GOOGLE_TRENDS]) or ok(st[YOUTUBE]))

    if candidate_type == POLICY:
        # 해당 candidate의 evidence가 나온 정책 소스가 GREEN이어야 함
        return ok(st[candidate.policy_source]) and ok(st[NAVER_NEWS]) and ok(st[NAVER_BLOG])

    if candidate_type == EVERGREEN:
        return (searchad_valid(st) and          # 기준월이 이번달 또는 지난달
                ok_daily(st[SEARCH_TREND]) and  # data ≤ D-1
                naver_supply_valid(st))         # Blog/Cafe GREEN
```

- YELLOW는 Gate 통과 불가다. Gate는 GREEN만 인정한다 (보수적).
- Gate FAIL이어도 점수는 계산·저장한다. `VERIFIED`/NOW 상태만 받지 못한다.

---

## 7. Fail-Closed 노출 규칙 (TODAY 화면)

TODAY 화면 렌더 시점에 다음을 순서대로 검사한다:

```
1. v_system_health에 RED 컴포넌트 존재?          → 상단 RED 배너
2. 최신 score snapshot의 freshness_pass = true?  → 아니면 #1 추천 숨김
3. 그 snapshot의 calculated_at이 2h 이내?        → 아니면 "추천 일시 중지" (파이프라인 정지로 간주)
4. 렌더 시점 실시간 소스 상태로 Gate 재평가       → 실패 시 #1 추천 숨김
```

숨김 시 표시 문구는 **deterministic하게 생성**한다 (Claude 미사용):

```
추천 일시 중지 — {source_name} 데이터가 {duration_h}시간 {duration_m}분 동안
갱신되지 않음 (마지막 성공: {last_success_kst} KST)
```

`duration = now - last_success_at`. 여러 소스가 문제면 가장 오래된 것 기준으로 문구를
만들고 전체 목록은 /health로 링크.

절대 금지 (명세 §64): 오래된 데이터의 GREEN 표시, collector 실패 무시하고 랭킹 노출.

---

## 8. 알려진 한계 (보증하지 않는 것)

문서화해 두는 이유: 이 한계를 "버그"로 오인해 SLA를 느슨하게 바꾸는 일을 막기 위해서다.

1. **Naver 인덱싱 지연**: `sort=date` 검색은 Naver가 인덱싱한 시점 기준이다. 원문 게시
   후 인덱싱까지의 지연은 우리 보증 밖이며, `source_data_through`는 "Naver 검색에
   노출된 문서 기준"이라는 의미다.
2. **Google Trends 편향**: Trending Now는 뉴스성 급등에 편향된다. 단독으로 높은 점수를
   만들지 않는 것은 scoring 규칙(명세 §10)이고, freshness와는 무관하다.
3. **GitHub Actions cron 지연**: 예약 시각과 실제 실행 시각의 차이는 SLA 안에 흡수되도록
   SLA가 실행주기의 1.5배 이상으로 설정돼 있다 ([WORKFLOWS.md](WORKFLOWS.md) §3).
4. **시계 정확도**: 모든 판정은 DB 서버(now()) 또는 runner의 UTC 시계 기준. 브라우저
   시계는 "현재:" 표시에만 쓰고 판정에는 쓰지 않는다.

---

## 9. 검증 (이 문서가 지켜지는지 확인하는 테스트)

[ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md)와 명세 §61에 매핑:

| 테스트 | 검증 내용 |
| --- | --- |
| Test A (Naver 강제 실패) | 180m 경과 후 RED, TODAY 추천 숨김 + 배너 문구 |
| Test C (Search Trend stale) | fetched_at 최신 + latest_data_date D-3 → RED |
| unit: `source_status` | 경계값 (89m/90m/180m/181m, D-1/D-2/D-3) 전수 |
| unit: `v_data_cutoff` | 소스 하나 뒤처짐 → min 반영, 미성공 소스 → NULL+RED |
| unit: gate | GREEN/YELLOW 조합 전수 (YELLOW는 불통과) |
| unit: 단조 증가 | data_through 후퇴 시도 → 유지 + 경고 |
