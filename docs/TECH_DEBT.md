# TECH_DEBT

Milestone 1/1.5에서 의도적으로 임시 처리한 항목. 각 항목은 해소 마일스톤이 지정되어
있으며, 해소 전까지 아래 제약이 유효하다. (M1.5 §15)

## A. Category Mapping — 임시

- **현황**: `workers/discovery/candidates.py`의 `_category_for()`가 seeds.yaml 키워드의
  literal 포함 검사로 category를 정한다. 예: "오즈모포켓4 축구촬영"이 camera가 아닌
  tech(fallback)로 분류됨.
- **제약**: category 기반 필터·모니터라이제이션 가중치를 신뢰 판단에 쓰지 않는다.
- **해소**: Milestone 2 — Claude semantic classification 보강 (명세 §2.1의 허용 용도:
  "키워드 의미 분류"). 단 분류 결과는 category 필드에만 반영, 점수 수치에는 불개입.

## B. blog_fit / monetization — constant stub

- **현황**: `workers/scoring/pipeline.py`의 opportunity 성분 중 `blog_fit`(70 고정),
  `monetization`(category 조건부 45/70)은 실제 intent 신호 없이 상수다.
- **제약**: **실제 Content Opportunity ranking에서 사용 금지.** 실 logic이 들어오기
  전까지 UI(Score Breakdown)에 `PROVISIONAL` 배지를 표시한다 (구현됨 —
  `apps/dashboard/app/candidate/[id]/page.tsx`). live 데이터 기반 추천을 켜는 시점에
  이 두 성분의 가중치(합 20)를 재검토해야 한다.
- **해소**: Milestone 2~3 — product/comparison/review intent 신호(명세 §52) 기반 계산.

## C. Term Extraction — whitespace 기반 (Mock 전용)

- **현황**: `workers/discovery/terms.py`는 공백 토큰 + 1~4 gram + stopword 목록이다.
  조사·어미가 붙는 실제 한국어 텍스트에서는 오분리·미탐이 필연적이다.
- **제약**: **실제 한국어 discovery에 사용 금지.** mock fixture는 이 한계에 맞게
  설계되어 있을 뿐이다.
- **해소**: **실 collector 단계 전에** Kiwi(또는 동급 maintained tokenizer) 구현
  (명세 §23). Milestone 2의 Naver collector 연결보다 먼저 들어가야 하는 선행 작업.

## D. 기타 (참고)

| 항목 | 현황 | 해소 |
| --- | --- | --- |
| `source_data_through` 단조 증가 강제 | 규칙만 문서화(DATA_FRESHNESS §1), collector 공통 프레임 미구현 | M2 실 collector 공통 프레임 |
| Supabase RLS의 DB-level 관리자 정책 | 미들웨어가 담당, `auth.jwt()` 정책은 0003 주석의 계획 상태 | M1.5 Supabase 연결 시 0005로 적용 |
| 대시보드 DB 접근 방식 | 서버 컴포넌트 + 직접 Postgres 연결(`DATABASE_URL`) — RLS를 경유하지 않음 | anon+RLS 경유로 전환할지 M2에서 결정 |
| Healthchecks 프로비저닝 스크립트 | 설계만(WORKFLOWS §3.1), scripts/provision_healthchecks.py 미구현 | M2 |
