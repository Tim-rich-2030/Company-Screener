# 이 디렉터리는 비어 있습니다 — 의도된 상태입니다

Company-Screener 는 **동면 상태**입니다. GitHub Actions 를 하나도 실행하지
않도록, 워크플로 파일을 전부 아래로 옮겨 두었습니다.

    archive/github-actions/

## 왜 '비활성화' 가 아니라 '이동' 인가

GitHub 은 **기본 브랜치의 `.github/workflows/` 에 있는 `.yml`/`.yaml`** 만
워크플로로 인식합니다. 여기에 파일이 없으면 어떤 방아쇠도 당겨지지 않습니다 —
예약(schedule)도, 수동 실행(workflow_dispatch)도, 푸시(push)도, API 호출도.

UI 의 Disable 버튼은 워크플로마다 따로 꺼야 하고 나중에 되살아나기 쉽습니다.
파일을 옮기면 목록 자체가 비므로 실수로 다시 켜질 자리가 없습니다.

이 파일은 `.md` 라 워크플로로 읽히지 않습니다. 디렉터리가 왜 비었는지
남겨 두려고 둡니다.

## 되살리려면

    git mv archive/github-actions/<파일>.yml .github/workflows/

기본 브랜치에 병합되는 순간부터 다시 인식됩니다. 되살리기 전에 각 파일의
`schedule` 을 확인하세요 — 여기 있던 것들은 5분~15분 주기로 돌던 것도
있습니다.

## 옮긴 것 (7개)

| 파일 | 이름 | 방아쇠 |
|---|---|---|
| `backfill.yml` | 과거 실적 소급 수집 | `7 * * * *` · dispatch |
| `board-live.yml` | 현황판 갱신 | `25 * * * *`, `5,45 21-23 * * *` · dispatch |
| `headline.yml` | 헤드라인 뉴스 | `*/15 * * * *` · dispatch |
| `market-signal.yml` | 시장 신호 수집 | `30 8 * * 1-5` · dispatch |
| `pages.yml` | 사이트 배포 | `push`(main, `docs/**`) · dispatch |
| `render-reel.yml` | 릴스 MP4 렌더링 | dispatch |
| `update.yml` | 실적 수집 및 사이트 갱신 | `0 10 * * 1-5`, `0 14 * * 1-5`, `0 1 * * 6` · dispatch |

## 이미 배포된 사이트는 그대로입니다

GitHub Pages 에 올라간 화면은 계속 서비스됩니다. 다만 `pages.yml` 이 없으므로
`docs/` 를 고쳐도 **자동 배포되지 않습니다.** 수집도 멈추므로 화면의 숫자는
동면에 들어간 시점에서 고정됩니다.
