# SETUP_REQUIRED

구현·운영 전에 **사용자가 직접 해야 하는** 계정 생성·키 발급·설정의 전체 목록.
순서대로 진행하면 된다. 각 항목의 산출물(Secret)은 GitHub 저장소
Settings → Secrets and variables → Actions에 등록한다 ([SECURITY.md](SECURITY.md) §1 표 참조).

> ⚠ 소요 시간 주의: **국민참여입법센터 OC는 관리자 승인 2~3일**이 걸린다.
> 정책 수집을 쓸 계획이면 가장 먼저 신청해 두는 것이 좋다.

## 1. Supabase

1. https://supabase.com → 프로젝트 생성 (리전: Northeast Asia 권장)
2. **플랜: Pro 권장.** 무료 플랜은 7일 저활동 시 프로젝트가 일시정지되어
   수집·watchdog이 전부 멈춘다 (공식 문서 확인됨). 무료로 시작한다면 이 리스크를
   수용하는 것임.
3. Settings → API에서 확보:
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (워커용)
   - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (대시보드용)
4. migration 적용은 구현 단계에서 (`supabase db push`).

## 2. Naver Open API — ⚠ 2026년 7월 이후 신규는 NAVER API HUB

2026-07-30부로 developers.naver.com에서 검색/검색어트렌드/쇼핑인사이트 API의
**신규 신청이 마감**되었다 (공식 약관 부칙 확인). 경로가 두 가지다:

- **기존에 developers.naver.com 애플리케이션이 있는 경우**: 그 Client ID/Secret을
  2027-06-30까지 그대로 사용 가능. → `NAVER_API_CLIENT_ID`, `NAVER_API_CLIENT_SECRET`
- **신규인 경우**: 네이버클라우드(NCP) 콘솔에서 **NAVER API HUB 이용신청**
  (https://www.ncloud.com/product/applicationService/naverApiHub).
  ⚠ API HUB의 인증 방식·엔드포인트·무료 쿼터·과금은 아직 우리 쪽에서 미검증이다
  ([DATA_SOURCES.md](DATA_SOURCES.md) §0). **키 발급 후 실제 호출 1회로 스키마를 확인하는
  것이 구현 1단계다.** 과금 여부를 신청 화면에서 반드시 확인할 것.

필요 API: 검색(뉴스/블로그/카페), 데이터랩 검색어트렌드, 데이터랩 쇼핑인사이트.

## 3. Naver Search Ads (검색광고)

1. https://searchad.naver.com 광고주 계정 가입 (사업자 없어도 개인 가입 가능)
2. 광고시스템(manage.searchad.naver.com) → 도구 → **API 사용 관리** → 네이버 검색광고
   API 서비스 신청 → 라이선스 생성
3. 확보: `NAVER_SEARCHAD_API_KEY`(액세스라이선스), `NAVER_SEARCHAD_SECRET_KEY`(비밀키),
   `NAVER_SEARCHAD_CUSTOMER_ID`(광고주 ID — 광고시스템 우상단)

## 4. YouTube Data API

1. https://console.cloud.google.com → 프로젝트 생성 → "YouTube Data API v3" 사용 설정
2. 사용자 인증 정보 → API 키 생성 → `YOUTUBE_API_KEY`
3. 참고: 2026-06 개편으로 **search.list는 기본 하루 100회** 전용 한도다. 우리 설계는
   80회/일 — 콘솔의 Quotas 화면에서 "Search Queries" 버킷을 확인해 둘 것.

## 5. 공공데이터포털 (data.go.kr)

1. 회원가입 → 로그인
2. 다음 API 활용신청 (자동승인):
   - 정책브리핑_정책뉴스_API (15095335)
   - 정책브리핑_보도자료_API (15095295)
   - (선택) 정책브리핑_전문자료_API (15125644)
3. 마이페이지에서 일반 인증키(serviceKey) 확인 → `DATA_GO_KR_SERVICE_KEY`
   (발급 직후 반영까지 최대 1시간 걸릴 수 있음)

## 6. 법제처 LAW OPEN DATA

1. https://open.law.go.kr 회원가입
2. OPEN API 신청 (활용 목적 작성) → API인증키관리에서 **OC 값** 확인 → `LAW_API_OC`

## 7. 국민참여입법센터 (입법예고/행정예고) — ⚠ 승인 2~3일

1. https://opinion.lawmaking.go.kr 회원가입
2. 정보공개 서비스(OPEN API) 이용 신청 → **관리자 승인 대기 (2~3일, 이메일 통보)**
3. OC = 로그인 ID (이메일 가입 시 @ 앞부분) → §6과 같은 `LAW_API_OC`를 쓰지 않고
   별도 값이면 `.env`에 주석으로 구분해 관리 (구현 시 소스 config에서 지정)

## 8. Anthropic API

1. https://console.anthropic.com → API 키 발급 → `ANTHROPIC_API_KEY`
2. 용도는 Brief/질문/Draft + 클러스터링 보조뿐이다. 이 키가 없어도
   수집~랭킹은 동작해야 정상이다 (명세 §61 Test E).

## 9. Healthchecks.io

1. https://healthchecks.io 가입 (무료 20 checks — 우리는 7개 사용)
2. 프로젝트 생성 → Settings에서 확보:
   - **Ping Key** → `HEALTHCHECKS_PING_KEY`
   - **API Key** (Management API, check 자동 생성용) → `HEALTHCHECKS_API_KEY`
3. Integrations에서 알림 채널 연결 (email 기본, 원하면 Slack/Telegram 추가)
4. check 생성은 수동으로 하지 않는다 — 구현된 `scripts/provision_healthchecks.py`가
   [WORKFLOWS.md](WORKFLOWS.md) §3.1의 Period/Grace로 자동 생성·갱신한다.

## 9.5 Vercel (Dashboard 배포 — 확정)

1. https://vercel.com 가입 → Add New Project → GitHub `content-radar` 저장소 연결
2. **Root Directory: `apps/dashboard`** 지정 (모노레포)
3. Environment Variables 설정:
   - `DATABASE_URL` — Supabase 연결 문자열 (Settings → Database → **Connection pooling**
     URI 권장, 서버 전용 — 클라이언트에 노출되지 않음)
   - `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `ADMIN_EMAILS` — 접근 허용 관리자 이메일 (콤마 구분)
   - `NEXT_PUBLIC_RADAR_ENV=PRODUCTION`
   - `AUTH_DISABLED`은 설정하지 않는다 (기본 false — 운영에서 true 금지)
4. Supabase 대시보드 → Authentication → Users에서 관리자 계정(이메일/비밀번호) 생성
   (Sign-up은 공개로 열지 않는다)
5. 배포 후 확인: 비로그인 접근 → /login 리다이렉트, allowlist 외 계정 → 차단,
   /health에 Environment=PRODUCTION·Git SHA 표시

## 9.6 GitHub Secrets — infra-heartbeat 검증용 (M1.5)

| Secret | 값 |
| --- | --- |
| `SUPABASE_DB_URL` | Supabase Postgres 연결 문자열 (Settings → Database) |
| `HEALTHCHECKS_PING_KEY` | (선택) Healthchecks 프로젝트 Ping Key — 없으면 ping 단계 생략 |

설정 후 Actions 탭에서 `infra-heartbeat` 워크플로를 Run workflow로 실행하면
GitHub → Supabase 기록/연결성/health 갱신 체인이 검증된다.

## 10. GitHub 저장소 설정

1. Settings → Secrets and variables → Actions에 위 Secret 전부 등록
2. Actions 활성화 확인 (Settings → Actions → Allow all actions)
3. 첫 가동: 각 워크플로를 Actions 탭에서 `Run workflow`(workflow_dispatch)로 1회씩
   수동 실행 → 대시보드 /workflows와 Healthchecks에서 기록 확인

## 11. (콘텐츠 발행 단계에서) Naver Brand Connect

시스템 설정은 아니지만 운영에 필요: 쇼핑커넥트 상품 확인·링크 발급은
Naver Brand Connect에서 사용자가 직접 한다. 발급한 URL을 대시보드의 해당 후보에
입력하면 Draft에 삽입된다 (명세 §8). 시스템은 링크를 자동 생성하지 않는다.

---

## 체크리스트 요약

- [ ] Supabase 프로젝트 (Pro 권장) + 키 4종
- [ ] Naver API HUB (또는 기존 developers.naver.com 앱) — **1순위로 신청, 스키마 검증이 구현 1단계**
- [ ] Naver Search Ads API 라이선스 3종
- [ ] YouTube API 키
- [ ] data.go.kr serviceKey (정책브리핑 2종 활용신청)
- [ ] 법제처 OC
- [ ] 국민참여입법센터 OC — **승인 2~3일, 조기 신청**
- [ ] Anthropic API 키
- [ ] Healthchecks.io Ping Key + API Key + 알림 채널
- [ ] GitHub Secrets 등록 + Actions 활성화
