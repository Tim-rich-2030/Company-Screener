# FAILURE_MODES

장애 시나리오별 **기대 동작**의 명세. 각 시나리오는 "감지 경로 → 시스템 동작 →
사용자가 보는 것"으로 정의하며, 표시된 것은 [ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md)에서
실제로 재현 테스트한다. 명세 §61의 Test A~E를 포함해 확장한 것이다.

공통 원칙:

- **Fail-Closed**: 확신할 수 없으면 추천하지 않는다. 그러나 시스템 전체를 멈추지도
  않는다 — 영향 범위를 소스/기능 단위로 격리한다.
- 장애는 항상 **데이터로 기록**된다 (`source_runs.error`, `system_health`, `alerts`).
  로그 파일에만 남는 장애는 없는 장애 취급한다.

---

## 1. 수집 계층

### F1. Naver Search API 실패 (명세 §61 Test A) — 필수 테스트

| 항목 | 내용 |
| --- | --- |
| 유발 | API 4xx/5xx, 타임아웃, 잘못된 키 |
| 감지 | `source_runs.status=failed` → watchdog/실시간 판정에서 90m 후 YELLOW, 180m 후 RED |
| 동작 | 다른 소스 수집은 계속. score는 계속 계산되나 Market Gate 불통과 → `freshness_pass=false` |
| 화면 | /health 해당 소스 RED, TODAY 상단 RED 배너 + "추천 일시 중지 — Naver Search 데이터가 N시간 M분 동안 갱신되지 않음", #1 추천(NOW 카드) 숨김 |
| 금지 | 기존 순위를 최신인 것처럼 노출, RED인데 GREEN 표시 |

### F2. GitHub Actions cron 미실행 (명세 §61 Test B) — 필수 테스트

| 항목 | 내용 |
| --- | --- |
| 유발 | 워크플로 수동 disable (테스트 시), GitHub 장애, 60일 비활성화 |
| 감지 | 3중: Healthchecks Period+Grace 초과 alert (이메일) / Supabase watchdog 120분 규칙 → system_health RED / DB에 새 run 부재 |
| 화면 | 대시보드 헤더 RED, /workflows에 "마지막 성공 N시간 전" |
| 주의 | 이 경우 DB에는 **아무 기록도 남지 않는다** — 그래서 Healthchecks·watchdog이 존재한다 |

### F3. API 성공했으나 데이터가 오래됨 (명세 §61 Test C) — 필수 테스트

| 항목 | 내용 |
| --- | --- |
| 예 | Search Trend 응답 정상이나 `latest_data_date`가 D-3 |
| 감지 | daily cadence 판정이 `source_data_through` 기준으로 동작 ([DATA_FRESHNESS.md](DATA_FRESHNESS.md) §3.2) |
| 동작 | `fetched_at` 최신이어도 상태 RED, Evergreen Gate 불통과 |
| 금지 | fetch 성공만 보고 GREEN 처리 |

### F4. 파서는 성공, 내용은 빈 응답 (silent breakage)

| 항목 | 내용 |
| --- | --- |
| 예 | API가 스키마를 바꿔 items가 항상 [] |
| 감지 | 고볼륨 소스 연속 3 run rows=0 → YELLOW + alert ([DATA_FRESHNESS.md](DATA_FRESHNESS.md) §3.4) |
| 동작 | contract test가 CI에서 fixture 스키마 검증 (배포 전 방어), 운영 중엔 위 휴리스틱 |

### F5. 쿼터 소진 (YouTube search 100/일, Naver 25,000/일, DataLab 1,000/일)

| 항목 | 내용 |
| --- | --- |
| 감지 | 403 quotaExceeded / 429 → `source_runs.error`에 코드 기록 |
| 동작 | **재시도 금지** (쿼터를 더 태움). 그 소스는 다음 리셋까지 수집 중단, 상태는 자연히 YELLOW→RED |
| 예방 | 호출 예산이 한도의 80% 이하가 되도록 root 수 제한 (DATA_SOURCES §1, §6). 예산 초과 예상 시 우선순위 낮은 root부터 스킵하고 그 사실을 run 로그에 기록 |

### F6. 중복 원문 폭주 (명세 §61 Test D) — 필수 테스트

| 항목 | 내용 |
| --- | --- |
| 예 | 동일 기사 신디케이션 10건 |
| 동작 | 1차 `(source_id, external_id)` UNIQUE, 2차 `content_hash` → 같은 사건을 10개 독립 evidence로 세지 않음. Cross Source Score는 syndication cluster를 1개 소스로 취급 (명세 §29) |
| 검증 | fixture로 10건 주입 → distinct_documents 및 cross_source 점수 확인 |

## 2. 계산·AI 계층

### F7. Claude API 실패 (명세 §61 Test E) — 필수 테스트

| 항목 | 내용 |
| --- | --- |
| 영향 범위 | **Brief/질문/Draft 생성만 실패.** 수집→점수→랭킹→대시보드는 완전 정상 (클러스터링은 규칙 기반 폴백) |
| 화면 | [글 만들기] 실행 시 "AI 서비스 일시 불가" + 재시도 버튼. TODAY/RADAR는 무영향 |
| 검증 | ANTHROPIC_API_KEY 무효화 후 파이프라인 전체 실행 → score snapshot 정상 생성 확인 |

### F8. Claude가 근거 없는 수치·문장을 생성

| 항목 | 내용 |
| --- | --- |
| 방어 1 | 추천 이유 문구는 deterministic facts로 생성, Claude 미사용 (명세 §40) |
| 방어 2 | Brief/Draft 출력 검증: 입력 facts 블록에 없는 수치(%, 배수, 검색량)가 출력에 있으면 재생성 1회 → 실패 시 해당 생성 실패 처리 |
| 방어 3 | 경험답변에 없는 1인칭 경험 서술 금지 프롬프트 + "경험 없음" 모드 (명세 §47) |

### F9. 점수 계산 자체의 버그

| 항목 | 내용 |
| --- | --- |
| 방어 | scoring은 순수 함수 + 경계값 unit test. `score_version`과 `components`(가중치 포함) 저장으로 사후 재계산·검증 가능 (Ranking reproducible) |
| 롤백 | snapshot은 append-only이므로 버그 버전의 snapshot은 남긴 채 새 버전으로 재계산. 화면은 최신 `score_version`만 사용 |

## 3. 저장·감시 계층

### F10. Supabase 접근 불가 / 프로젝트 일시정지

| 항목 | 내용 |
| --- | --- |
| 유발 | 무료 플랜 7일 저활동 정지 (검증됨), 장애, 키 만료 |
| 감지 | collector가 DB 연결 실패 → workflow failed + Healthchecks /fail. watchdog 자체도 정지 → `watchdog-supabase` ping 부재 alert |
| 동작 | 수집 데이터는 유실 (다음 성공 run에서 `sort=date` 페이지네이션으로 공백 메움 — Naver `start≤1000` 창 내에서) |
| 예방 | **Pro 플랜 전제** (Known Risk 명시) |

### F11. Healthchecks.io 장애

| 항목 | 내용 |
| --- | --- |
| 영향 | 감시 공백만 발생. 수집·점수·대시보드 무영향 (ping 실패는 무시하고 진행 — 감시자 장애가 파이프라인을 막지 않는다) |
| 잔여 감시 | watchdog → system_health는 계속 동작 |

### F12. Watchdog(pg_cron)만 사망

| 항목 | 내용 |
| --- | --- |
| 감지 | `watchdog-supabase` check의 ping 부재 → Healthchecks alert (15m Period + 15m Grace) |
| 영향 | system_health 갱신 지연. 대시보드는 `checked_at`이 30분 이상 오래되면 "감시 데이터 지연" 표시 (watchdog의 신선도도 표시 대상) |

### F13. 시간 처리 버그 (가장 흔한 실수 예방)

| 규칙 | 검증 |
| --- | --- |
| DB는 전부 UTC, 표시만 KST | unit test: KST 경계(자정 전후) 날짜 집계 |
| naive datetime 금지 | lint: `datetime.now()` (tz 없는) 사용 금지 규칙 |
| `retrieved_at`≠데이터 날짜 | F3 테스트가 커버 |

## 4. 장애 대응 우선순위 (사용자 관점 runbook 요약)

1. 대시보드 헤더 RED → /health에서 어느 컴포넌트인지 확인
2. 소스 RED → /workflows에서 해당 워크플로 최근 run의 error 확인 →
   키 만료/쿼터/스키마 변경 중 무엇인지 error 메시지로 판단
3. 워크플로 자체가 안 돎 (run 기록 없음) → GitHub Actions 탭에서 워크플로
   enabled 여부 확인 → `Run Now`(workflow_dispatch)로 수동 실행
4. 전부 정상인데 추천 없음 → Freshness Gate 정상 동작 중일 수 있음 —
   TODAY의 중지 사유 문구가 원인 소스를 명시한다
