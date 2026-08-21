# WORKFLOWS

GitHub Actions 스케줄링·실행 증적·감시 체계의 전체 설계.
목표는 하나다: **"각 워크플로가 실제로 돌았는가"를 GitHub 화면을 열지 않고
대시보드만 보고 확정할 수 있어야 한다** (명세 §14~§17, §42~§44, §58, §61 Test B).

검증 근거: 이 문서의 GitHub Actions / Supabase Cron / Healthchecks.io 관련 사실은
2026-08-21 기준 공식 문서로 확인했다. 출처는 [DATA_SOURCES.md](DATA_SOURCES.md) §9~§10.

---

## 1. 워크플로 목록

| 파일 | 스케줄 (KST) | cron (`timezone: Asia/Seoul`) | 역할 | Healthchecks slug |
| --- | --- | --- | --- | --- |
| `collect-market.yml` | 매시 17분 | `17 * * * *` | Naver News/Blog/Cafe, Google Trends 수집 | `collect-market` |
| `collect-policy.yml` | 짝수시 37분 | `37 */2 * * *` | 정책브리핑, 법령, 입법·행정예고 수집 | `collect-policy` |
| `collect-youtube.yml` | 02:23 / 08:23 / 14:23 / 20:23 | `23 2,8,14,20 * * *` | YouTube 신규 영상 수집 (root 20개) | `collect-youtube` |
| `validate-demand.yml` | 매일 06:47 | `47 6 * * *` | Search Trend, Shopping Insight, Search Ads (Top 20~30) | `validate-demand` |
| `score-and-rank.yml` | 매시 32분 | `32 * * * *` | discovery + scoring + freshness gate + snapshot | `score-and-rank` |
| `daily-report.yml` | 매일 07:13 | `13 7 * * *` | TODAY Ranking 확정 | `daily-report` |

정각(:00)을 피하는 이유: GitHub 공식 문서가 "매시 정각은 고부하 시간대로 지연·드롭
가능성이 높다"고 명시한다. 분 오프셋(17/23/32/37/47/13)은 명세 그대로 유지한다.

### 1.1 Timezone — 2026-03 이후 방식

GitHub Actions는 2026년 3월부터 `schedule`에 IANA timezone을 네이티브 지원한다:

```yaml
on:
  schedule:
    - cron: '17 * * * *'
      timezone: "Asia/Seoul"
  workflow_dispatch:
    inputs:
      test_mode:
        type: boolean
        default: false
```

- 기본은 여전히 UTC이므로 `timezone` 명시를 **모든 워크플로에 강제**한다 (PR 체크리스트 항목).
- KST는 DST가 없으므로 spring-forward 보정 규칙의 영향 없음.
- 만약 이 필드가 어떤 이유로 동작하지 않는 환경이면(구버전 GHES 등) UTC 등가 cron으로
  폴백한다: KST−9h. 예: `47 6 * * *` KST → `47 21 * * *` UTC(전일).

### 1.2 GitHub cron의 알려진 성질 (설계가 이미 흡수한 것)

| 성질 | 대응 |
| --- | --- |
| 스케줄 실행은 지연될 수 있고 드물게 드롭됨 | Freshness SLA를 실행주기의 1.5~2배로 설정, Healthchecks Grace로 흡수, 드롭은 watchdog이 감지 |
| 스케줄 워크플로는 default branch에서만 실행 | 워크플로 파일은 `main`에만 존재. 브랜치 작업 중에도 main의 cron이 계속 돈다 |
| public repo에서 60일 비활성 시 스케줄 자동 비활성화 | 저장소는 private이지만, 비활성화가 발생하면 Healthchecks가 즉시 잡는다 (ping 중단 → alert). 추가로 daily-report가 매일 커밋 없이도 돌므로 활동 기준과 무관하게 감시망 유지 |
| 최소 간격 5분 | 우리 최소 주기는 1시간 — 여유 |

---

## 2. 공통 실행 프레임 (모든 워크플로 동일)

모든 워크플로는 같은 뼈대를 쓴다. 단일 진입점 `python -m workers.run <name>`이
아래 라이프사이클을 책임진다. **워크플로 YAML에는 로직을 넣지 않는다** — YAML은
체크아웃 + 의존성 설치 + 진입점 호출 + 최후의 실패 ping뿐이다.

### 2.1 라이프사이클

```
1. [YAML]   Healthchecks /start ping  (curl, 실패해도 진행 — 감시자 장애가 수집을 막으면 안 됨)
2. [Python] workflow_runs INSERT
            (run_id, workflow_name, github_run_id, github_sha, trigger_type,
             scheduled_at, started_at, status='running')
3. [Python] 소스별 수집 루프:
            source_runs INSERT(running) → collect → UPDATE(success/failed,
            http_status, rows_received, rows_new, source_data_through, error)
            ※ 소스 하나의 실패가 다른 소스 수집을 중단시키지 않는다
4. [Python] workflow_runs UPDATE
            (completed_at, duration_seconds, status, error_message,
             items_received, items_new)
            status = success | partial | failed
            - success: 전 소스 성공
            - partial: 일부 소스 실패 (workflow 자체는 exit 0, 실패는 source_runs에)
            - failed : 프레임 자체가 죽음 (DB 연결 불가 등)
5. [Python] Healthchecks 종료 ping:
            success → https://hc-ping.com/<PING_KEY>/<slug>
            partial → 동일 success ping + POST body에 실패 소스 요약
            failed  → …/<slug>/fail
            POST body(≤10KB)에 구조화 로그 요약: run_id, 소스별 rows, 에러 첫 줄
6. [YAML]   step 실패 시 최후 방어: if: failure() 스텝에서 …/<slug>/fail ping
            (Python이 뜨기도 전에 죽은 경우 — pip 실패, DB secret 오류 등)
```

`scheduled_at`은 cron 예정 시각(워크플로가 아는 자기 스케줄), `started_at`은 실제 시작
시각. 이 차이가 GitHub 지연의 측정값이 되어 WORKFLOWS 화면에 표시된다.

### 2.2 partial을 두는 이유

Naver Cafe 하나가 죽었다고 workflow 전체를 fail 처리하면, Healthchecks에서
"collect-market 전체 장애"로 보인다. 실제로는 News/Blog가 정상 수집됐다.
소스 단위 상태는 `source_runs`→`system_health`가 정확히 들고 있으므로
(그리고 그 소스는 Freshness에서 RED가 되므로), workflow 레벨은 "돌긴 돌았다"를
보고하는 게 맞다. 단 **전 소스 실패면 workflow도 failed**다.

### 2.3 공통 YAML 설정 (명세 §58 전 항목 매핑)

```yaml
timeout-minutes: 15            # collect-*, score-and-rank
timeout-minutes: 30            # validate-demand (Search Ads 순차 조회)
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: false    # 수집 중단은 데이터 공백을 만든다. 대기 후 스킵이 안전
permissions:
  contents: read               # 워크플로는 repo에 쓸 일이 없다
```

| 명세 §58 항목 | 구현 위치 |
| --- | --- |
| timeout-minutes | YAML (위) |
| concurrency | YAML (위) |
| secrets | `${{ secrets.* }}` → env. [SECURITY.md](SECURITY.md) |
| retry + exponential backoff | Python HTTP 클라이언트 공통 래퍼: 3회, 2s/4s/8s, `Retry-After` 존중. 429는 재시도하되 quota 소스(YouTube/Search Ads)는 재시도 금지 |
| request timeout | 모든 외부 호출 connect 5s / read 30s |
| structured logging | JSON lines stdout → Actions 로그 + 요약은 Healthchecks POST body |
| 실패 시 DB run status 기록 | 라이프사이클 4단계 + try/finally 보장 |
| Healthchecks failure ping | 라이프사이클 5~6단계 (이중) |
| workflow_dispatch | 전 워크플로 필수 (§3) |
| test mode | dispatch input `test_mode=true` → fixture 사용·외부 호출 생략, `trigger_type='test'`로 기록, Healthchecks ping은 `/log` 엔드포인트로 보내 Period 판정 오염 방지 |

### 2.4 실행 순서 의존성

`score-and-rank`(:32)는 `collect-market`(:17) **이후**를 가정하지만, 강한 의존을 걸지
않는다 (workflow_run 트리거 미사용). 이유: collect가 15분 늦어도 score는 "그 시점까지의
데이터"로 정확한 snapshot을 만들고, `data_complete_through`가 그 사실을 그대로 드러낸다.
Freshness 모델이 순서 문제를 데이터로 흡수하는 구조다. `daily-report`(07:13)는
`validate-demand`(06:47) 직후로 배치했으며 같은 원리로 소프트 의존이다.

---

## 3. Healthchecks.io 구성

slug 방식 ping을 쓴다: `https://hc-ping.com/<PING_KEY>/<slug>`.
Secret은 `HEALTHCHECKS_PING_KEY` 하나면 된다 (check별 UUID 관리 불필요).

### 3.1 Check 정의 (config/healthchecks.yml → Management API upsert 스크립트로 생성)

| slug | 방식 | Period/Schedule | Grace | 근거 |
| --- | --- | --- | --- | --- |
| `collect-market` | Simple | 1 hour | 30 min | 명세 §16 예시 그대로 |
| `collect-policy` | Simple | 2 hours | 1 hour | 주기 2h |
| `collect-youtube` | Cron `23 2,8,14,20 * * *` (tz Asia/Seoul) | — | 2 hours | 하루 4회 비등간격 → Cron 타입이 정확 |
| `validate-demand` | Cron `47 6 * * *` (tz Asia/Seoul) | — | 3 hours | 일 1회 |
| `score-and-rank` | Simple | 1 hour | 30 min | 주기 1h |
| `daily-report` | Cron `13 7 * * *` (tz Asia/Seoul) | — | 2 hours | 일 1회 |
| `watchdog-supabase` | Simple | 15 min | 15 min | §4의 watchdog 자체도 감시 대상 |

- 무료 플랜 한도 20 checks — 우리는 7개. 여유 있음.
- check 생성은 대시보드 수동이 아니라 `scripts/provision_healthchecks.py`가
  Management API(v3, `unique: ["slug"]` upsert)로 만든다 — 재현 가능성(명세 §65).
- `/start` ping을 반드시 보낸다 → Healthchecks가 실행시간을 측정하고, start 후
  Grace 내 성공 ping이 없으면 "죽은 채 시작만 한 run"도 잡아낸다.
- 알림 채널: email 기본, 사용자가 원하는 채널 추가 ([SETUP_REQUIRED.md](SETUP_REQUIRED.md)).

### 3.2 Healthchecks가 잡아내는 실패 모드

| 실패 모드 | 감지 경로 |
| --- | --- |
| cron이 아예 안 돎 (GitHub 드롭/비활성화) | ping 부재 → Period+Grace 초과 → alert (명세 §61 Test B) |
| run이 시작 후 행/타임아웃 | /start 후 성공 ping 부재 → Grace 초과 → alert |
| run이 명시적으로 실패 | /fail ping → 즉시 alert |
| Python이 뜨기 전에 사망 | YAML `if: failure()` 스텝의 /fail ping |
| GitHub Actions 서비스 전체 장애 | ping 부재 → alert (DB 기록도 없지만 감시는 작동) |

---

## 4. Supabase Cron Watchdog (제3의 독립 감시자)

Healthchecks가 "ping이 왔는가"를 보고, watchdog은 "**DB에 실제 성공 기록이
있는가**"를 본다. 서로 다른 것을 검증하므로 중복이 아니다 — ping은 성공했지만
DB 기록이 안 남는 버그(트랜잭션 실패 등)는 watchdog만 잡는다.

- pg_cron 기반 Supabase Cron job, **15분마다**, DB 내부에서 실행 (GitHub와 완전 독립).
- 구현: `supabase/migrations`의 SQL function `run_watchdog()` + `cron.schedule('watchdog', '*/15 * * * *', ...)`. pg_cron 스케줄은 UTC지만 등간격이므로 무관.
- 검사 (명세 §17 그대로 + 확장):

```sql
collect-market  마지막 success > 120분  → system_health RED + alerts INSERT
collect-policy  마지막 success > 240분  → RED
score-and-rank  마지막 success > 120분  → RED
collect-youtube 마지막 success > 8시간  → RED
validate-demand 마지막 success > 26시간 → RED
각 source의 §3(DATA_FRESHNESS) 상태 재계산 → system_health UPSERT
```

- watchdog 자신도 마지막에 `net.http_post`(pg_net)로 `watchdog-supabase` slug에
  ping을 보낸다 → **watchdog이 죽으면 Healthchecks가 잡는다.** 감시 사슬의 끝이
  외부 서비스에 닿게 하는 것이 원칙이다.
- `alerts`에는 동일 component의 미해소(resolved_at IS NULL) alert가 있으면 중복
  INSERT하지 않는다. 상태가 회복되면 resolved_at을 채운다.

**리스크 (검증됨)**: Supabase 무료 플랜은 7일 저활동 시 프로젝트가 일시정지되며,
내부 cron 활동만으로는 정지를 확실히 막지 못한다. 정지되면 DB도 collector도 전부
멈춘다 (이 경우에도 Healthchecks ping 부재로 alert는 발생). **대응: Pro 플랜 사용을
기본 전제로 한다.** 무료로 시작한다면 이 리스크를 수용하는 것임을 명시한다.
→ ARCHITECTURE STATUS의 Known Risks 항목.

---

## 5. 수동 실행 (Run Now)

- 모든 워크플로에 `workflow_dispatch` (필수, 명세 §15·§44).
- V1 대시보드 WORKFLOWS 화면은 각 워크플로의 GitHub dispatch 페이지로 가는 링크를
  제공한다: `https://github.com/<owner>/content-radar/actions/workflows/<file>.yml`.
  대시보드 내 직접 트리거 버튼(GitHub API 호출)은 PAT 관리가 필요하므로 V1 범위 밖.
- dispatch 실행도 동일한 라이프사이클을 타며 `trigger_type='manual'`로 기록된다.
- `test_mode` input: fixture 기반 실행. DB에는 `trigger_type='test'`로 남고
  Healthchecks Period 판정을 오염시키지 않는다 (§2.3).

---

## 6. 대시보드 WORKFLOWS 화면 (명세 §43)

데이터 출처는 `workflow_runs` + `source_runs`뿐이다. GitHub API를 호출하지 않는다.

워크플로별 최근 20개 run:

| 컬럼 | 원본 필드 |
| --- | --- |
| Scheduled / Started / Completed | scheduled_at, started_at, completed_at (지연 = started−scheduled 표시) |
| Duration | duration_seconds |
| Status | success / partial / failed / running (+ running인데 30분 경과 시 "stalled?" 표시) |
| Items / New | items_received / items_new |
| Trigger | schedule / manual / test |
| GitHub run | github_run_id → `https://github.com/<owner>/content-radar/actions/runs/<id>` 링크 |
| Commit | github_sha (short) 링크 |
| Sources | 해당 run의 source_runs 요약 (성공 n/m, 실패 소스명) — 행 확장 시 상세 |

상단에는 워크플로별 "다음 예정 실행"과 "마지막 성공"을 표시한다.

---

## 7. "실제로 돌았는가" 검증 체인 — 전체 그림

세 층이 서로 다른 질문에 답하고, 어느 한 층의 장애도 다른 층이 보고한다.

```
층 1  DB 실행 기록 (workflow_runs / source_runs)
      → "무엇이 언제 돌아 몇 건을 가져왔나" (대시보드의 원천)
      맹점: GitHub이 아예 안 돌면 기록 자체가 없다

층 2  Healthchecks.io heartbeat
      → "ping이 제때 왔는가" (부재 감지 = 층 1의 맹점 해소)
      맹점: ping은 왔지만 DB 기록이 실패한 버그

층 3  Supabase Cron watchdog (15분)
      → "DB에 신선한 성공 기록이 실제로 있는가" (층 2의 맹점 해소)
      맹점: Supabase 자체 정지 → 층 2가 watchdog-supabase ping 부재로 감지
```

| 장애 시나리오 | 층 1 | 층 2 | 층 3 | 사용자가 보는 것 |
| --- | --- | --- | --- | --- |
| GitHub cron 드롭/비활성화 | 기록 없음 | **alert** | **RED** | 이메일 + 대시보드 RED |
| collector 예외로 실패 | failed 기록 | **fail ping alert** | RED | 대시보드에 에러 메시지 |
| 소스 1개만 실패 | partial + source_runs | (success ping+요약) | 해당 소스 RED | /health에 소스 RED, TODAY Gate 반영 |
| ping은 성공, DB 기록 누락 (버그) | 기록 없음 | 정상으로 보임 | **RED** | 대시보드 RED |
| Supabase 정지 | 접근 불가 | **watchdog ping 부재 alert** | 정지 | 이메일 |
| Healthchecks 장애 | 정상 | 침묵 | 정상 | 대시보드는 정상 작동 (감시 공백만 발생) |

이 표의 각 행이 [FAILURE_MODES.md](FAILURE_MODES.md)의 테스트 시나리오와 1:1로
매핑되며, 최소 Test A/B(명세 §61)는 배포 전 실제로 수행한다.
