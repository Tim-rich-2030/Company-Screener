"""단위 테스트: 파싱·계산·보고서 선택 로직 (네트워크 불필요)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import kospi_value_screener as k

# --- to_num ---
assert k.to_num("1,234,567") == 1234567
assert k.to_num("-1,000") == -1000
assert k.to_num("(2,500)") == -2500
assert k.to_num("") is None
assert k.to_num("-") is None
assert k.to_num(None) is None
assert k.to_num("△1,000") == -1000
print("to_num ok")

# --- norm_name ---
assert k.norm_name("지배기업의 소유주에게 귀속되는 자본") == "지배기업의소유주에게귀속되는자본"
assert k.norm_name(" 자본  총계 ") == "자본총계"
print("norm_name ok")

# --- preferred detection ---
assert k.is_preferred_name("삼성전자우")
assert k.is_preferred_name("현대차2우B")
assert k.is_preferred_name("대신증권우")
assert not k.is_preferred_name("삼성전자")
assert not k.is_preferred_name("한국앤컴퍼니")   # '우'로 끝나지 않음
codes = ["005930", "005935", "000660", "005380", "005387"]
names = {"005930": "삼성전자", "005935": "삼성전자우", "000660": "SK하이닉스",
         "005380": "현대차", "005387": "현대차2우B"}
pm = k.build_preferred_map(codes, names)
assert pm["005930"] is True and pm["005380"] is True
assert pm["000660"] is False
assert pm["005935"] is False   # 우선주 자체는 플래그 대상 아님
print("preferred ok")

# --- period candidates ---
for base, expect_first in [("20260728", "2026 1Q"),
                           ("20260901", "2026 2Q"),
                           ("20260301", "2025 3Q"),
                           ("20260401", "2025 FY"),
                           ("20261201", "2026 3Q")]:
    ps = k.build_period_candidates(base)
    got = [p.label for p in ps[:4]]
    assert got[0] == expect_first, (base, got)
    print(f"  {base} -> {got}")
# 분기가 연간보다 앞서는지 (2026 1Q > 2025 FY)
labels = [p.label for p in k.build_period_candidates("20260728")]
assert labels.index("2026 1Q") < labels.index("2025 FY")
print("periods ok")

# --- PeriodResolver ---
ps = k.build_period_candidates("20260728")
r = k.PeriodResolver(ps)
first = ps[0]
for _ in range(k.UNAVAILABLE_STRIKES):
    r.mark(first, False)
assert first not in r.order(), "미공시 보고서가 계속 조회됨"
assert len(r.order()) <= k.MAX_PERIOD_TRIES
print("resolver ok")

# --- parse_balance_sheet ---
payload = {"status": "000", "list": [
    {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
     "thstrm_amount": "1,000,000"},
    {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent",
     "account_nm": "지배기업의 소유주에게 귀속되는 자본", "thstrm_amount": "400,000"},
    {"sj_div": "BS", "account_id": "ifrs-full_NoncontrollingInterests",
     "account_nm": "비지배지분", "thstrm_amount": "50,000"},
    {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
     "thstrm_amount": "450,000"},
    {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
     "thstrm_amount": "999"},
]}
res = k.parse_balance_sheet(payload)
assert res.parent_equity == 400000 and res.equity_total == 450000, res
print("parse BS (연결) ok")

# account_id 없이 계정명만 있는 경우 (별도재무제표/구형 응답)
payload2 = {"status": "000", "list": [
    {"sj_div": "BS", "account_id": "-", "account_nm": "자본총계", "thstrm_amount": "77,000"},
    {"sj_div": "BS", "account_id": "-", "account_nm": "비지배지분", "thstrm_amount": "1,000"},
]}
res2 = k.parse_balance_sheet(payload2)
assert res2.equity_total == 77000 and res2.parent_equity is None, res2
print("parse BS (별도) ok")

# 미제출 보고서
res3 = k.parse_balance_sheet({"status": "013", "message": "조회된 데이타가 없습니다."})
assert res3.equity_total is None and "013" in res3.reason
print("parse BS (미제출) ok")

# --- 계산식 sanity ---
equity = 450_000 * 1_000_000        # 4,500억
shares = 10_000_000
mcap = 315_000 * 1_000_000          # 3,150억
bps = equity / shares
calc = mcap / equity
assert abs(bps - 45000.0) < 1e-6
assert abs(calc - 0.7) < 1e-9
gap = (calc - 0.75) / 0.75 * 100
assert abs(gap - (-6.6666666)) < 1e-4
print("math ok")

# --- 키 마스킹 ---
k.remember_secret("abc123SECRET")
msg = "url=https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=abc123SECRET (fail)"
out = k.redact(msg)
assert "abc123SECRET" not in out and "REDACTED" in out, out
# 등록 전 키라도 crtfc_key= 패턴이면 가려진다
out2 = k.redact("GET /api/x.json?crtfc_key=neverRegistered&corp_code=00126380")
assert "neverRegistered" not in out2 and "corp_code=00126380" in out2, out2
# 제외 사유(CSV로 저장됨)에도 키가 남지 않는다
e = k.Excluded()
e.add("005930", "삼성전자", "DART조회", "실패 crtfc_key=abc123SECRET", verbose=False)
assert "abc123SECRET" not in e.summary().iloc[0]["사유"]
print("redact ok")

# --- API 키 입력 방어 ---
# 코랩의 getpass는 통신 실패 시 문자열이 아닌 dict를 돌려준다.
os.environ.pop("DART_API_KEY", None)
for bad, label in [({"error": "x"}, "dict"), (None, "None"), (123, "int")]:
    try:
        k._accept_key(bad, "입력창")
    except SystemExit as exc:
        assert "DART_API_KEY" in str(exc), exc          # 조치 방법을 알려주는지
        assert type(bad).__name__ in str(exc), exc      # 받은 타입을 알려주는지
    else:
        raise AssertionError(f"{label} 입력을 걸러내지 못했습니다")
# 빈 문자열도 거부
try:
    k._accept_key("   ", "입력창")
except SystemExit as exc:
    assert "비어 있습니다" in str(exc), exc
else:
    raise AssertionError("빈 키를 걸러내지 못했습니다")
# 정상 키는 통과하고, 재실행 시 입력창을 다시 띄우지 않도록 환경변수에 저장된다
assert k._accept_key("  realkey123  ", "입력창") == "realkey123"
assert os.environ["DART_API_KEY"] == "realkey123"
assert k.get_api_key() == "realkey123"       # 두 번째 호출은 입력창 없이 환경변수에서
assert "realkey123" not in k.redact("crtfc_key=realkey123")
os.environ.pop("DART_API_KEY", None)
print("api key handling ok")

print("\nALL TESTS PASSED")
