# SECURITY

Secrets 관리, DB 접근 제어, 수집 윤리. 명세 §13, §50, §59, §64 대응.

## 1. Secrets

### 원칙

- 실제 secret은 어디에도 커밋되지 않는다. 저장소에는 `.env.example`만 존재한다 (명세 §65).
- GitHub Actions에서는 **GitHub Secrets → env** 주입만 사용. 로그에 secret이 찍히지
  않도록 구조화 로깅에서 헤더/키 필드를 마스킹한다.
- Healthchecks POST body(외부 전송 로그 요약)에는 에러 메시지 첫 줄만 넣고,
  URL·헤더 전문은 넣지 않는다 (serviceKey가 쿼리스트링에 있는 data.go.kr류 API 주의 —
  에러 기록 시 URL에서 `serviceKey/OC/KEY` 파라미터를 제거하는 공통 sanitizer를 거친다).

### Secrets 목록 (GitHub Secrets 이름 = .env 이름)

| Secret | 용도 | 노출 범위 |
| --- | --- | --- |
| `SUPABASE_URL` | 워커/대시보드 공통 | URL 자체는 비밀 아님 (관례상 secret로 관리) |
| `SUPABASE_SERVICE_ROLE_KEY` | 워커 전용 | **RLS 우회 전권 키. Actions에서만. 프론트 금지** |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | 대시보드 | 공개 전제 (RLS가 방어선) |
| `NAVER_API_CLIENT_ID/SECRET` | 검색/DataLab | collectors |
| `NAVER_SEARCHAD_API_KEY/SECRET_KEY/CUSTOMER_ID` | 키워드도구 | validate-demand |
| `YOUTUBE_API_KEY` | YouTube | collect-youtube |
| `DATA_GO_KR_SERVICE_KEY` | 정책브리핑 등 | collect-policy |
| `LAW_API_OC` | 법제처/입법예고 | collect-policy |
| `ANTHROPIC_API_KEY` | Brief/Draft | content 워커만 |
| `HEALTHCHECKS_PING_KEY` | heartbeat | 전 워크플로 (ping 전용 키 — Management API 키와 분리) |
| `HEALTHCHECKS_API_KEY` | check 프로비저닝 | 프로비저닝 스크립트 실행 시에만, cron 워크플로에는 미주입 |

키 로테이션: 어떤 키든 유출 의심 시 해당 콘솔에서 재발급 → GitHub Secrets 갱신.
코드 변경 불필요한 구조를 유지한다 (키를 코드에서 파생·조합하지 않음).

## 2. Supabase 접근 제어 (RLS)

- 모든 테이블 RLS ON. 역할별 권한:

| 역할 | 권한 |
| --- | --- |
| `service_role` (워커) | 전권. GitHub Actions와 로컬 개발에서만 |
| `anon` (대시보드) | [DATABASE.md](DATABASE.md) §5의 view SELECT만 + 아래 쓰기 3종 |

- anon 쓰기 허용 3종은 각각 **RPC 함수(SECURITY DEFINER)** 로만 연다 — 테이블 직접
  INSERT/UPDATE 정책을 열지 않는다:
  - `submit_feedback(candidate_id, verdict)` — verdict CHECK 검증
  - `save_experience_answer(brief_id, question_order, answer)` — 길이 제한
  - `set_shopping_connect_url(candidate_id, url)` — **URL 검증: naver.com 계열 도메인의
    https만 허용** (비공식 링크 자동 생성 금지 원칙의 입력단 방어, 명세 §64)
- 대시보드 접근 제어 (**확정**): **Supabase Auth 사용, 지정된 관리자 이메일 계정만
  로그인 가능.** URL만 아는 사람에게 단순 공개하는 방식 금지. 미들웨어에서 세션 없으면
  /login으로 리다이렉트하고, 로그인 후에도 이메일이 관리자 목록(`ADMIN_EMAILS`)에
  없으면 접근 거부. anon key는 프론트 사용 가능(RLS 방어), service_role key는
  프론트 절대 금지.

## 3. 수집 윤리·법적 제약 (명세 §13, §64)

- **공식 API·공식 RSS만 사용한다.** V1의 enabled 소스 중 HTML 스크레이핑은 없다.
- HTML Source Adapter(향후)는 다음을 코드로 강제한다:
  robots.txt 확인, 로그인·CAPTCHA 우회 금지, fetch interval ≥ 2h,
  `If-None-Match`/`If-Modified-Since` 지원 시 사용, User-Agent에 프로젝트 식별자 명시.
- **Naver 자동 로그인/자동 발행 금지** — V1 범위에서 구현 자체가 없다 (명세 §50).
- 쿼터 존중: [FAILURE_MODES.md](FAILURE_MODES.md) F5 — 쿼터 초과 시 재시도 금지.
- Shopping Connect 링크는 사용자가 Brand Connect에서 발급한 URL의 수동 입력만 허용
  (자동 생성 금지, 명세 §8·§64). Draft에는 disclosure 문구를 항상 포함 (명세 §49).

## 4. AI 입출력 경계

- Claude에 전달하는 데이터: 후보의 deterministic facts, evidence 제목/발췌(공개 데이터),
  사용자 경험답변. **API 키·사용자 이메일 등은 프롬프트에 포함하지 않는다.**
- Claude 출력 검증: [FAILURE_MODES.md](FAILURE_MODES.md) F8 (근거 없는 수치 차단).
- 수집 원문(외부 텍스트)을 프롬프트에 넣을 때는 데이터로 취급한다 — 원문 내 지시문이
  Brief/Draft 생성 지침을 바꾸지 못하도록 시스템 프롬프트에서 명시한다
  (prompt injection 방어의 기본선).

## 5. 저장소 위생

- `.gitignore`에 `.env*` (example 제외). CI에 secret scanning:
  push 시 `sk-ant-`, `AIza`, JWT 패턴 등 기본 패턴 검사 (GitHub secret scanning +
  pre-commit 훅).
- fixture 저장 시 실제 응답에서 **개인 식별 정보·본인 키 제거** 후 커밋
  (fixture sanitizer 스크립트 경유).
- Supabase service_role 키는 로컬 `.env`에만 — 절대 브라우저 코드/`NEXT_PUBLIC_*`로
  넘기지 않는다 (빌드 시 lint 규칙으로 `NEXT_PUBLIC_` 접두사에 service role 문자열
  대입을 차단).
