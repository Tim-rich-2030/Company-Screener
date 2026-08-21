#!/usr/bin/env bash
# Secret Scan (M1.5 §2) — working tree + 전체 git history에서 credential 패턴 검사.
# 발견 시 exit 1. CI의 secret-scan job과 로컬에서 동일하게 사용한다.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

# 패턴: API key / token / service key / JWT / healthchecks ping URL / 비밀번호 포함 DSN
PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{20,}'                 # Anthropic API key
  'AIza[0-9A-Za-z_-]{35}'                     # Google API key
  'gh[pousr]_[A-Za-z0-9]{36,}'                # GitHub token
  'github_pat_[A-Za-z0-9_]{20,}'              # GitHub fine-grained PAT
  'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}' # JWT (Supabase key 등)
  'hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}'      # Healthchecks UUID ping URL
  'hc-ping\.com/[A-Za-z0-9_-]{20,}/'          # Healthchecks ping key URL (실키)
  'postgres(ql)?://[^/[:space:]"]+:[^@/[:space:]"]+@' # 비밀번호 포함 연결 문자열
  'SERVICE_ROLE_KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9]' # 값이 채워진 service role
  'X-Naver-Client-Secret[[:space:]]*:[[:space:]]*[A-Za-z0-9]'
)

FAIL=0
for p in "${PATTERNS[@]}"; do
  # 1) working tree (스캐너 자신의 패턴 정의는 제외)
  hits=$(git grep -InE "$p" -- ':!scripts/secret_scan.sh' 2>/dev/null)
  if [ -n "$hits" ]; then
    echo "[secret-scan] WORKING TREE 패턴 발견: $p"
    echo "$hits"
    FAIL=1
  fi
  # 2) 전체 history (추가된 라인만)
  hhits=$(git log --all -p --no-color -G "$p" -- ':!scripts/secret_scan.sh' 2>/dev/null \
          | grep -E "^\+.*" | grep -E "$p" | grep -v '^+++' | head -5)
  if [ -n "$hhits" ]; then
    echo "[secret-scan] GIT HISTORY 패턴 발견: $p"
    echo "$hhits"
    FAIL=1
  fi
done

# 3) .env가 트래킹되지 않는지
if git ls-files | grep -qE '(^|/)\.env$'; then
  echo "[secret-scan] .env 파일이 git에 트래킹됨"
  FAIL=1
fi

# 4) .env.example에 값이 채워진 secret 변수가 없는지 (KEY= 뒤가 비어있거나 placeholder만 허용)
bad_env=$(grep -E '^[A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*=..+' .env.example 2>/dev/null || true)
if [ -n "$bad_env" ]; then
  echo "[secret-scan] .env.example에 값이 채워진 변수:"
  echo "$bad_env"
  FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  echo "[secret-scan] PASS — credential 패턴 없음 (working tree + full history)"
fi
exit $FAIL
