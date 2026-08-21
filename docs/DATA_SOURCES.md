# DATA_SOURCES

외부 데이터 소스별로 **2026-08-21 기준 공식 문서에서 검증된 사실**과 **미검증 항목**을
분리해 기록한다. 구현은 VERIFIED 항목만 근거로 하며, UNVERIFIED 항목은 구현 착수 전
fixture 확보(실제 호출 1회 → `fixtures/`에 저장)로 해소해야 한다. **추측 구현 금지**
(명세 §0 지시사항).

검증 방법 주석: 이 세션의 네트워크에서 일부 공식 사이트 직접 접근이 차단되어,
Naver는 공식 GitHub 저장소(naver/naver-openapi-guide, naver/searchad-apidoc — 각 문서
사이트의 원본), Google/GitHub/Supabase/Healthchecks는 공식 문서·공식 discovery
document, 한국 정부 API는 공식 페이지의 검색 노출 내용으로 검증했다. 아래 각 절에
출처를 명시한다.

---

## 0. ⚠ 최우선 사실: Naver Open API의 NAVER API HUB 이관

출처: developers.naver.com 이용약관 부칙 (naver/naver-openapi-guide 커밋 2026-07-31),
NCP 공지 (2026-06-25 NAVER API HUB 출시).

| 시점 | 내용 |
| --- | --- |
| 2026-06-25 | NCP(네이버클라우드)에 **NAVER API HUB** 출시 — 검색/트렌드/쇼핑인사이트 API의 새 창구 |
| 2026-07-30 24:00 | developers.naver.com에서 검색·검색어트렌드·쇼핑인사이트 API **신규 신청 마감** |
| 2026-07-31 24:00 | **Search API의 쇼핑(shop)·책·전문자료 vertical 완전 종료** (기존 사용자 포함) |
| 2027-06-30 24:00 | 기존 developers.naver.com 키 사용 종료 예정 |
| 2027-07-01 | 이관 대상 API는 NAVER API HUB 전용 |

**우리에게 의미하는 것:**

1. 신규 사용자는 **NCP 콘솔의 NAVER API HUB로 신청**해야 한다
   (https://www.ncloud.com/product/applicationService/naverApiHub).
   → [SETUP_REQUIRED.md](SETUP_REQUIRED.md) §2.
2. 명세 §8의 전제(Shopping Search API 종료) **공식 확인됨**. 쇼핑커넥트 상품
   자동 선택 불가 → 사용자 수동 URL 입력 흐름 유지.
3. UNVERIFIED: API HUB의 엔드포인트/스키마가 기존 openapi.naver.com과 동일한지,
   무료 쿼터·과금. **구현 첫 단계에서 API HUB 키 발급 후 실제 호출로 확인 필수.**
   아래 §1~§3의 스키마는 developers.naver.com(openapi.naver.com) 기준이며, API HUB가
   같은 스키마를 쓴다는 가정은 아직 검증되지 않았다.

---

## 1. Naver Search API — News / Blog / Cafe

출처: naver/naver-openapi-guide `ko/service-apis/search/*` (developers.naver.com 문서 원본).

**공통 (VERIFIED):**

- `GET https://openapi.naver.com/v1/search/{news|blog|cafearticle}.json`
- 인증 헤더: `X-Naver-Client-Id`, `X-Naver-Client-Secret`
- 파라미터: `query`(필수, UTF-8), `display`(기본 10, **최대 100**),
  `start`(기본 1, **최대 1000**), `sort` = `sim`(기본) | **`date`(최신순, 지원 확인)**
- 쿼터: **25,000 calls/day** (검색 API 합산, client ID 기준)
- 응답 공통: `lastBuildDate`, `total`, `start`, `display`, `items[]`.
  제목/본문의 검색어는 `<b>` 태그로 감싸짐 → 정규화 시 제거 필요.
- 에러: SE01(잘못된 쿼리)~SE99, 403 = 앱에 해당 API 미설정.

**Vertical별 응답 필드 (VERIFIED) — 시간 해상도 차이에 주의:**

| Vertical | 필드 | 게시시각 |
| --- | --- | --- |
| News | `title, originallink, link, description, pubDate` | `pubDate` = RFC-1123 (+0900), **초 단위**. "Naver에 기사가 제공된 시각" |
| Blog | `title, link, description, bloggername, bloggerlink, postdate` | `postdate` = **`yyyymmdd` 날짜만** (시각 없음) |
| Cafe | `title, link, description, cafename, cafeurl` | **날짜 필드 없음** |

→ 이 차이의 처리 규칙은 [DATABASE.md](DATABASE.md) §2 (`published_precision`)에 정의.

**수집 전략:** root keyword × vertical로 `sort=date` 조회, 직전 `source_data_through`와
겹칠 때까지 페이지네이션. `start` 최대 1000 = keyword당 최대 1000건 창.

**쿼터 예산:** root keyword N개 × 3 vertical × 24회/일 = 72N calls/day.
**root는 40~50개로 시작한다 (확정)** — 45개 기준 3,240 calls/day로 25,000 한도의 13%.
`config/seeds.yaml`에서 관리하며 코드 hardcode 금지. category 14종에 priority 부여.

## 2. Naver DataLab Search Trend (검색어트렌드)

출처: 동일 저장소 `ko/service-apis/datalab/search/search.md`.

- `POST https://openapi.naver.com/v1/datalab/search`, JSON body, 동일 인증 헤더 (VERIFIED)
- 쿼터: **1,000 calls/day** (VERIFIED)
- body: `startDate`(≥2016-01-01), `endDate`, `timeUnit`(`date|week|month`),
  `keywordGroups`(**최대 5그룹**, 그룹당 keywords **최대 20개**), 옵션 `device/gender/ages`
  (VERIFIED — ages 코드는 1~11 구간)
- 응답: `results[].data[]` = `{period, ratio}`. **ratio는 기간 내 최대=100인 상대지수**
  (절대 검색량 아님 — 공식 문서 명시, 명세 §7.2 그대로) (VERIFIED)
- **UNVERIFIED: 데이터 지연(D-1 여부)이 공식 문서에 없음.** →
  `latest_data_date`는 응답의 마지막 `period`에서 읽어 저장하고, 실측으로 SLA의
  `expected_lag_days`를 보정한다. fetch 성공 ≠ 데이터 최신 (명세 §61 Test C).
- 호출 예산: 후보 상위권 위주. 1회 호출로 5그룹 → 하루 200회 호출 = 1,000그룹 커버 가능.

## 3. Naver DataLab Shopping Insight

출처: 동일 저장소 `ko/service-apis/datalab/shopping/shopping.md`.

- 8개 엔드포인트 모두 `POST`, JSON body, 동일 인증, 쿼터 **1,000 calls/day** (VERIFIED)
- 주요: `/v1/datalab/shopping/categories` (카테고리 최대 3개 비교),
  `/v1/datalab/shopping/category/keywords` (키워드 그룹 최대 5, **그룹당 키워드 1개**)
- `category`는 shopping.naver.com URL의 `cat_id` (CID) — `config/categories.json`에 매핑
- `ages` 코드가 검색어트렌드와 **다름** (`10,20,...,60`) — 혼용 금지 (VERIFIED)
- 성격: 쇼핑 분야 **클릭 추이 상대지수**. 상품 목록 API 아님 (명세 §7.3 확인됨)
- UNVERIFIED: 데이터 지연 — §2와 동일하게 실측 보정.

## 4. Naver Search Ads Keyword Tool

출처: naver/searchad-apidoc (공식 저장소: README, gh-pages Swagger spec, 공식 Python 샘플).

- Base **`https://api.searchad.naver.com`**, `GET /keywordstool` (VERIFIED)
- 인증 (VERIFIED, 공식 샘플 기준): 헤더 `X-Timestamp`(ms), `X-API-KEY`, `X-Customer`,
  `X-Signature` = Base64(HMAC-SHA256(secret, `"{timestamp}.{method}.{uri}"`)), uri는 path만
- 파라미터: **`hintKeywords` 콤마구분 최대 5개**, `showDetail=1` (VERIFIED)
- 응답 `keywordList[]` (VERIFIED): `relKeyword`,
  `monthlyPcQcCnt` / `monthlyMobileQcCnt` — **최근 30일 합산, 문자열이며 10 미만은
  `"< 10"` 리터럴** → 파싱 규칙: `"< 10"` → 5로 저장 + 원문 raw 보존,
  `monthlyAve*ClkCnt/Ctr`(최근 4주), `plAvgDepth`, `compIdx`(`low|mid|high`)
- 발급: searchad.naver.com 가입 → 광고시스템 → 도구 > API 사용 관리 (VERIFIED)
- **UNVERIFIED: 쿼터/속도 제한 수치 없음** (에러 1014 "limit exceeded"만 존재).
  → 보수적으로 초당 1회 이하 + 하루 Top 20~30 후보만 조회 (명세 §9). 429/1014 시
  백오프 후 그 날은 중단 (재시도로 밀어붙이지 않음).
- 분류: **Demand Base 지표** — 실시간 freshness 판정 제외 ([DATA_FRESHNESS.md](DATA_FRESHNESS.md) §2).

## 5. Google Trends Trending Now (RSS)

출처: 공식 Trends Help(3076011)의 Export=RSS 안내 + 현행 파서들의 실사용 URL.

- **현행 URL: `https://trends.google.com/trending/rss?geo=KR`** (2024 개편 후 체계).
  구 URL(`/trends/trendingsearches/daily/rss`)은 사망 — pytrends는 2025-04 아카이브됨 (VERIFIED)
- item 구조 (VERIFIED, ns `xmlns:ht`): `title`(검색어), `pubDate`, `ht:approx_traffic`
  (예 "500,000+"), `ht:news_item[]`(`news_item_title/url/snippet/source`), `ht:picture`
- traffic_bucket ← `approx_traffic`, related ← `news_item` 매핑.
  `growth_percent`는 RSS에 없음 → **저장 필드는 nullable**, 명세 §10의 필드 중 RSS가
  주지 않는 것은 비워둔다 (추측 금지)
- 공식 Google Trends API(2025-07 알파 발표)는 **신청제 알파, 사용 불가** — Trending Now
  데이터도 아님 (VERIFIED)
- UNVERIFIED: 이 세션에서 라이브 fetch가 차단되어 **현재 시점 응답을 직접 확인 못 함**;
  `hours=` 파라미터는 실사용 코드에 있으나 비공식. → 구현 첫 단계에서 실호출로 fixture
  확보. 요청 빈도는 시간당 1회(우리 스케줄)로 충분히 보수적.

## 6. YouTube Data API v3

출처: 공식 discovery document(rev 20260820, 라이브 fetch 성공) + 공식 quota 문서.

- **⚠ 2026-06-01 쿼터 개편 (VERIFIED):** `search.list`는 **전용 쿼터 버킷**으로 분리 —
  기본 **하루 100회의 search.list 호출** (1 call = 1 unit in Search Queries bucket).
  기타 엔드포인트는 별도 10,000 units/day. `videos.list`는 1 unit → 통계 보강은 사실상 무제한.
- 우리 계획 20 root × 4회 = 80 search calls/day → **한도 100의 80%. 여유 20회뿐이므로
  재시도는 쿼터 버킷을 소모함을 코드에 명시** (429/403 quotaExceeded 시 재시도 금지, 명세 §11 유지)
- `search.list` 파라미터 (VERIFIED): `q`, `publishedAfter`(RFC3339), `order=date`,
  `regionCode=KR`, `relevanceLanguage=ko`, `type=video`, `maxResults`≤50, `part=snippet`
- 응답 (VERIFIED): `id.videoId`, `snippet.publishedAt/title/description/channelId/channelTitle`
- 쿼터 리셋: 자정 Pacific Time. 확장은 공식 audit 절차뿐 (기대하지 않음)

## 7. 정책 소스

### 7.1 정책브리핑 (korea.kr)

- data.go.kr 공식 API 4종 존재 (VERIFIED): 정책뉴스(15095335) —
  `http://apis.data.go.kr/1371000/policyNewsService/policyNewsList`,
  보도자료(15095295) — `.../pressReleaseService/pressReleaseList`, 전문자료(15125644), 포토(15095300)
- 인증: data.go.kr `serviceKey`, 활용신청 자동승인 (VERIFIED). `startDate/endDate` 파라미터 존재 (VERIFIED)
- 공식 RSS 병행 존재 (VERIFIED — 목록 페이지 기준): `https://www.korea.kr/rss/policy.xml` 등
- **UNVERIFIED: 응답 필드 정확명(승인일 필드 등), 페이지네이션 파라미터, 갱신주기,
  RSS 라이브 응답.** → 키 발급 후 실호출 fixture로 확정. RSS는 API 장애 시 보조 수단.

### 7.2 법제처 LAW OPEN DATA (open.law.go.kr)

- 목록: `http://www.law.go.kr/DRF/lawSearch.do?OC={OC}&target={target}&type=XML|JSON` (VERIFIED)
- 본문: `.../DRF/lawService.do?OC={OC}&target=law&ID=...` (VERIFIED)
- target: `law`(현행법령), **`eflaw`(시행일 기준 — 시행예정 포함)**, `admrul`, `ordin` 등 (VERIFIED)
- 파라미터: `query, display(≤100), page, sort=ddes, ancYd=YYYYMMDD~YYYYMMDD(공포일 범위),
  efYd(시행일 범위), org(소관부처)` (VERIFIED — 공식 가이드/실예시)
- 목록 응답 필드 (VERIFIED): `법령일련번호, 법령명한글, 공포일자, 제개정구분명, 시행일자, 법령상세링크`
- 신규/개정 감지 패턴: 별도 "최근 제개정" 엔드포인트는 **없음(확인 범위 내)** —
  `target=law + ancYd 최근범위 + sort=ddes` (신규 공포) 와 `target=eflaw + efYd 범위`
  (시행 예정) 두 쿼리로 커버
- 인증: open.law.go.kr 가입 → OPEN API 신청 → **OC 키** (API인증키관리에서 확인) (VERIFIED)
- UNVERIFIED: 속도 제한/약관 수치, 전체 필드 목록 → fixture로 확정

### 7.3 입법예고 / 행정예고 (국민참여입법센터)

- 입법예고 목록: `https://www.lawmaking.go.kr/rest/ogLmPp.xml?OC={OC}&...`
  (공식 샘플 `OC=test&lsClsCd=AA0103&diff=0` 확인) (VERIFIED)
- 행정예고 목록: `http://www.lawmaking.go.kr/rest/ptcpAdmPp/` (VERIFIED)
- 파라미터: `OC`(필수), `lsClsCd`(법령종류), `diff=0`(진행중), `cptOfiOrgCd`(소관부처), `lsNm` (VERIFIED)
- 인증: opinion.lawmaking.go.kr 가입 → 정보공개 서비스 신청 → OC = 로그인 ID,
  **관리자 승인 2~3일 소요** (VERIFIED — 즉시 발급 아님, 일정에 반영)
- UNVERIFIED: 응답 필드명(법령안명/예고기간/소관부처 태그), JSON 지원 여부 → fixture로 확정

### 7.4 국회 의안 (BILL_PROPOSED) — V1 선택 소스

- 열린국회정보 `https://open.assembly.go.kr/portal/openapi/{API_CODE}`, KEY 발급제 (VERIFIED)
- 신규 발의 감지: `BILLRCP`(접수목록) 또는 발의법률안 API + `PROPOSE_DT` 필터
- UNVERIFIED: 코드별 정확 스키마. **V1 기본 disable** — Source Registry에 등록만 하고
  fixture 확보 후 enable (명세 §13의 확장 구조로 처리).

## 8. HTML Source Adapter (명세 §13)

V1에는 **구현 구조만** 만들고 기본 enable 소스는 없다. enable 조건(공식 사이트,
robots 검토, 로그인/CAPTCHA 우회 금지, interval ≥ 2h, ETag/Last-Modified 활용)은
Source Registry의 `config`에 명시하고 코드가 강제한다.

## 9. GitHub Actions (스케줄러 — 검증 요약)

출처: docs.github.com (events-that-trigger-workflows, workflow-syntax), GitHub Changelog 2026-03.

- **`schedule`에 IANA `timezone` 필드 지원 (2026-03 추가, VERIFIED)** — `Asia/Seoul` 직접 지정 가능
- 5-field POSIX cron, 최소 5분 간격, 비표준(@daily 등) 미지원 (VERIFIED)
- 스케줄 실행은 default branch에서만, 고부하 시간(정각)에 지연·드롭 가능 (VERIFIED)
- public repo 60일 비활성 시 스케줄 자동 비활성화 (VERIFIED — 우리는 private, §WORKFLOWS 참조)
- `timeout-minutes` 기본 360, `concurrency` 그룹 지원 (VERIFIED)
- 적용 설계는 [WORKFLOWS.md](WORKFLOWS.md).

## 10. Supabase Cron / Healthchecks.io (감시자 — 검증 요약)

출처: supabase.com/docs (cron, pg_cron, project pausing), healthchecks.io/docs.

- Supabase Cron = pg_cron, DB 내부 실행, SQL/`net.http_post` 호출 가능, 분 단위 이하도 지원 (VERIFIED)
- 권고: 동시 8 job 이하, job당 10분 이하; `cron.job_run_details`는 자동정리 안 됨 (VERIFIED)
- **무료 플랜 7일 저활동 시 프로젝트 일시정지 — 내부 cron만으로 정지 방지 보장 없음
  (VERIFIED)** → Known Risk, Pro 플랜 전제
- Healthchecks: `hc-ping.com/<uuid|ping_key/slug>` + `/start` `/fail` `/log`,
  POST body 로그(한도는 `Ping-Body-Limit` 헤더로 확인), slug `?create=1` 자동 프로비저닝
  (기본 Period 1d/Grace 1h → Management API로 교정), 무료 20 checks, 분당 ~5회 초과 ping
  rate-limit (VERIFIED)
- 적용 설계는 [WORKFLOWS.md](WORKFLOWS.md) §3~§4.

---

## 11. UNVERIFIED 총목록 (구현 전 해소 절차 포함)

| # | 항목 | 해소 방법 |
| --- | --- | --- |
| 1 | NAVER API HUB의 스키마 동일성·쿼터·과금 | API HUB 신청 → 실호출 1회 → fixture 저장 → §1~§3 갱신 |
| 2 | DataLab 2종의 데이터 지연(D-1?) | 실호출로 `latest_data_date` 실측 → SLA `expected_lag_days` 확정 |
| 3 | Search Ads 쿼터 수치 | 보수 정책(초당≤1, 일 20~30 조회)으로 회피 + 1014 에러 관찰 |
| 4 | Google Trends RSS 현재 응답 | 실호출 → fixture. 실패 시 대안 없음 → 소스 disable + Cross Source 가중치는 그대로(0점 처리) |
| 5 | 정책브리핑 API 응답 필드/페이지네이션, RSS 라이브 | serviceKey 발급 → 실호출 fixture |
| 6 | 법제처 필드 전체 목록·속도 제한 | OC 발급 → 실호출 fixture |
| 7 | 입법·행정예고 응답 필드 | OC 승인(2~3일) 대기 → 실호출 fixture |
| 8 | 국회 의안 API 스키마 | V1 disable, 추후 fixture 확보 시 enable |

**규칙: fixture 없는 소스는 Source Registry에서 enable할 수 없다.** contract test
(명세 §60)가 fixture를 요구하므로 이 규칙은 CI에서 자동 강제된다.
