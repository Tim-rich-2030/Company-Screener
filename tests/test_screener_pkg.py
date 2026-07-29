"""
자동 수집 파이프라인 테스트 (네트워크 불필요).

집중해서 보는 것:
  1) 공시 감지가 정기보고서만 골라내고 정정본을 올바로 다루는가
  2) 시계열 저장이 기존 값을 덮어쓰지 않는가 (덮어쓰면 과거가 조용히 바뀐다)
  3) 과거 분기 지표가 '그때의 주가'로 계산되는가 (오늘 주가로 계산하면 착시)

실행:  python tests/test_screener_pkg.py
"""
import os
import sys
import json
import shutil
import tempfile
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from screener import config as cfg

_TMP = tempfile.mkdtemp(prefix="screener_store_")
cfg.STORE_DIR = _TMP
cfg.FACTS_DIR = os.path.join(_TMP, "facts")
cfg.STATE_PATH = os.path.join(_TMP, "state.json")
cfg.SITE_DIR = os.path.join(_TMP, "docs")

from screener import store, watch, ingest, prices, site   # noqa: E402
from screener.metrics import QuarterContext, compute_timeseries  # noqa: E402
import quarterly_dashboard as qd                          # noqa: E402

EOK = 100_000_000


def approx(a, b, tol=1e-6):
    return a is not None and abs(float(a) - float(b)) < tol


# =============================================================================
# 1) 공시 감지
# =============================================================================

def test_parse_report_name():
    assert watch.parse_report_name("분기보고서 (2025.03)") == {
        "year": 2025, "reprt_code": "11013", "quarter": 1,
        "kind": "분기보고서", "period_month": "03"}
    assert watch.parse_report_name("분기보고서 (2025.09)")["reprt_code"] == "11014"
    assert watch.parse_report_name("반기보고서 (2025.06)")["reprt_code"] == "11012"
    assert watch.parse_report_name("사업보고서 (2025.12)")["reprt_code"] == "11011"
    # 정정 공시도 인식해야 한다 — 정정본에 실제 수치 수정이 들어온다
    assert watch.parse_report_name("[기재정정]분기보고서 (2025.09)")["quarter"] == 3
    # 정기보고서가 아닌 것은 무시
    for other in ("주요사항보고서(유상증자결정)", "임원ㆍ주요주주특정증권등소유상황보고서",
                  "매출액또는손익구조30%(대규모법인은15%)이상변동", ""):
        assert watch.parse_report_name(other) is None, other
    print("test_parse_report_name: OK")


class FakeListClient:
    """DART list.json 을 흉내낸다."""
    calls = 0
    last_error = ""

    def __init__(self, items, pages=1):
        self.items, self.pages = items, pages

    def get_json(self, endpoint, params):
        assert endpoint == "list.json"
        assert params.get("pblntf_ty") == "A", "정기공시만 요청해야 합니다"
        page = int(params["page_no"])
        if page > self.pages:
            return {"status": "013"}
        return {"status": "000", "total_page": self.pages, "list": self.items}


def disclosure(rcept, code, name, report_nm, corp="00000001"):
    return {"rcept_no": rcept, "corp_code": corp, "corp_name": name,
            "stock_code": code, "report_nm": report_nm, "rcept_dt": rcept[:8]}


def test_detect_filters_and_dedupes():
    items = [
        disclosure("20251114000001", "005930", "삼성전자", "분기보고서 (2025.09)"),
        disclosure("20251114000002", "005930", "삼성전자", "[기재정정]분기보고서 (2025.09)"),
        disclosure("20251114000003", "000660", "SK하이닉스", "분기보고서 (2025.09)"),
        disclosure("20251114000004", "", "비상장사", "사업보고서 (2025.12)"),
        disclosure("20251114000005", "005380", "현대차", "주요사항보고서(유상증자결정)"),
    ]
    hits = watch.detect(FakeListClient(items), seen=set(), today=dt.date(2025, 11, 14))
    by_code = {h["code"]: h for h in hits}

    assert set(by_code) == {"005930", "000660"}, by_code
    # 같은 회사·같은 분기가 둘이면 접수번호가 큰 정정본만 남는다
    assert by_code["005930"]["rcept_no"] == "20251114000002"
    assert by_code["005930"]["qkey"] == "2025Q3"
    assert by_code["005930"]["reprt_code"] == "11014"

    # 이미 본 접수번호는 다시 잡지 않는다 (매 실행마다 재수집하면 안 된다)
    again = watch.detect(FakeListClient(items),
                         seen={"20251114000001", "20251114000002", "20251114000003"},
                         today=dt.date(2025, 11, 14))
    assert again == [], again
    print("test_detect_filters_and_dedupes: OK")


# =============================================================================
# 2) 저장소
# =============================================================================

def test_merge_does_not_overwrite():
    rec = {"code": "005930", "name": "삼성전자", "quarters": {}}
    assert store.merge_quarter(rec, "2025Q3", {"매출액": 100.0, "영업이익": 10.0})
    # 같은 분기가 다음 해 보고서의 '전년 동기'로 다시 들어와도 기존 값을 지키다
    assert not store.merge_quarter(rec, "2025Q3", {"매출액": 999.0})
    assert rec["quarters"]["2025Q3"]["매출액"] == 100.0
    # 비어 있던 항목은 채운다
    assert store.merge_quarter(rec, "2025Q3", {"순이익": 8.0})
    assert rec["quarters"]["2025Q3"]["순이익"] == 8.0
    # 알 수 없는 분기 라벨은 거부
    assert not store.merge_quarter(rec, "2025Q9", {"매출액": 1.0})
    assert not store.merge_quarter(rec, "쓰레기", {"매출액": 1.0})
    print("test_merge_does_not_overwrite: OK")


def test_sort_quarters():
    keys = ["2024Q4", "2025Q1", "2023Q2", "2025Q4", "2025Q2"]
    assert store.sort_quarters(keys)[0] == "2025Q4"
    assert store.sort_quarters(keys)[-1] == "2023Q2"
    assert store.sort_quarters(keys, newest_first=False)[0] == "2023Q2"
    print("test_sort_quarters: OK")


def test_save_load_roundtrip_and_trim():
    rec = {"code": "999999", "name": "테스트", "quarters": {}}
    for y in range(2018, 2027):
        for q in (1, 2, 3, 4):
            store.merge_quarter(rec, f"{y}Q{q}", {"매출액": float(y * 10 + q)})
    store.save(rec)
    back = store.load("999999")
    assert len(back["quarters"]) == cfg.KEEP_QUARTERS, len(back["quarters"])
    # 오래된 분기부터 잘라낸다
    assert store.sort_quarters(back["quarters"])[0] == "2026Q4"
    print("test_save_load_roundtrip_and_trim: OK")


def test_price_snapshot_is_immutable():
    rec = {"code": "005930", "name": "삼성전자", "quarters": {}}
    store.merge_quarter(rec, "2025Q3", {"매출액": 100.0})
    assert store.set_price(rec, "2025Q3", 70000, 5_000_000_000, "20250930", "naver")
    slot = rec["quarters"]["2025Q3"]
    assert slot["시가총액"] == 70000 * 5_000_000_000
    # 이미 굳은 과거 스냅샷은 다시 쓰지 않는다 (오늘 주가로 덮이면 시계열이 무너진다)
    assert not store.set_price(rec, "2025Q3", 99999, 5_000_000_000, "20260729", "naver")
    assert slot["종가"] == 70000
    print("test_price_snapshot_is_immutable: OK")


# =============================================================================
# 3) 분기 지표 — '그때의 주가'로 계산되는가
# =============================================================================

def sample_record():
    rec = {"code": "005930", "name": "테스트전자", "quarters": {}}
    # 8분기: 매출 100억, 영업이익 10억, 순이익 8억 (분기당)
    for y, q in [(2026, 1), (2025, 4), (2025, 3), (2025, 2),
                 (2025, 1), (2024, 4), (2024, 3), (2024, 2)]:
        store.merge_quarter(rec, f"{y}Q{q}", {
            "매출액": 100 * EOK, "영업이익": 10 * EOK,
            "순이익": 8 * EOK, "지배주주순이익": 8 * EOK,
            "지배주주지분": 1000 * EOK, "자본총계": 1100 * EOK,
            "자산총계": 3000 * EOK, "부채총계": 2000 * EOK,
        })
    # 주가 스냅샷: 2026Q1 은 2000억, 2025Q1 은 1000억 (같은 실적, 다른 주가)
    caps = {"2026Q1": 2000, "2025Q4": 1800, "2025Q3": 1600, "2025Q2": 1400,
            "2025Q1": 1000, "2024Q4": 900, "2024Q3": 800, "2024Q2": 700}
    for qkey, eok_cap in caps.items():
        store.set_price(rec, qkey, eok_cap * EOK / 10_000_000, 10_000_000,
                        qkey.replace("Q", "0") + "00", "test")
    return rec


def test_quarter_context_uses_that_quarters_price():
    rec = sample_record()
    quarters = store.sort_quarters(rec["quarters"])
    assert quarters[0] == "2026Q1" and quarters[4] == "2025Q1"

    now = QuarterContext(rec, quarters, 0)
    past = QuarterContext(rec, quarters, 4)

    # 자기자본·실적은 같고 시총만 2배 -> PBR 도 정확히 2배여야 한다
    assert approx(now.equity, 1000 * EOK) and approx(past.equity, 1000 * EOK)
    assert approx(now.mcap / past.mcap, 2.0)
    assert approx(qd.METRICS["PBR"].fn(now), 2.0)       # 2000억 / 1000억
    assert approx(qd.METRICS["PBR"].fn(past), 1.0)      # 1000억 / 1000억

    # TTM 은 그 분기에서 과거로 4개 -> 32억
    assert approx(now.ttm("지배주주순이익"), 32 * EOK)
    assert approx(qd.METRICS["PER"].fn(now), 2000 / 32)
    assert approx(qd.METRICS["PER"].fn(past), 1000 / 32)
    # ROE 는 주가와 무관하므로 두 시점이 같아야 한다
    assert approx(qd.METRICS["ROE(%)"].fn(now), 3.2)
    assert approx(qd.METRICS["ROE(%)"].fn(past), 3.2)
    print("test_quarter_context_uses_that_quarters_price: OK")


def test_timeseries_shape_and_yoy():
    rec = sample_record()
    rec["quarters"]["2026Q1"]["영업이익"] = 15 * EOK      # 전년 동기(2025Q1)는 10억
    ts = compute_timeseries(rec, qd.METRICS)
    assert ts["quarters"][0] == "2026Q1"
    for key, vals in ts["metrics"].items():
        assert len(vals) == len(ts["quarters"]), key
    assert approx(ts["metrics"]["매출 YoY(%)"][0], 0.0)
    assert approx(ts["metrics"]["영업이익(억)"][0], 15)
    assert approx(ts["metrics"]["분기 영업이익률(%)"][0], 15.0)
    # 시계열 값은 소수 4자리로 반올림해 저장하므로 그만큼 여유를 둔다
    assert approx(ts["metrics"]["ROA(%)"][0], 32 / 3000 * 100, tol=1e-3)
    # 가장 오래된 분기는 TTM 4개가 안 되므로 PER 이 없어야 한다
    assert ts["metrics"]["PER"][-1] is None
    print("test_timeseries_shape_and_yoy: OK")


# =============================================================================
# 4) 사이트
# =============================================================================

def test_site_build():
    rec = sample_record()
    store.save(rec)
    path = site.build(cfg.SITE_DIR)
    doc = open(path, encoding="utf-8").read()

    assert "테스트전자" in doc and "005930" in doc
    assert '"companies"' in doc and '"metrics"' in doc
    # 자체 완결형이어야 GitHub Pages·로컬 어디서든 동일하게 뜬다
    assert "https://" not in doc and "<script src" not in doc and "<link" not in doc
    assert "prefers-color-scheme" in doc
    assert os.path.exists(os.path.join(cfg.SITE_DIR, ".nojekyll"))
    print("test_site_build: OK")


# =============================================================================
# 5) 수집 (DART 응답 흉내)
# =============================================================================

class FakeDart:
    calls = 0
    last_error = ""

    def get_json(self, endpoint, params):
        if endpoint == "stockTotqySttus.json":
            return {"status": "000", "list": [
                {"se": "보통주", "isu_stock_totqy": "10,000,000"},
                {"se": "우선주", "isu_stock_totqy": "1,000,000"}]}
        if params.get("fs_div") != "CFS":
            return {"status": "013"}
        q = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[params["reprt_code"]]
        cum = {1: 100, 2: 250, 3: 420, 4: 600}[q]
        return {"status": "000", "list": [
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
             "thstrm_add_amount": str(cum * EOK), "frmtrm_add_amount": str((cum - 40) * EOK)},
            {"sj_div": "BS", "account_id": "ifrs-full_Equity",
             "account_nm": "자본총계", "thstrm_amount": str(1000 * EOK)},
            {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent",
             "account_nm": "지배기업의 소유주에게 귀속되는 자본",
             "thstrm_amount": str(900 * EOK)},
        ]}


def test_ingest_one():
    rec = store.load("111111")
    hit = {"code": "111111", "corp_code": "00000001", "name": "수집테스트",
           "year": 2025, "reprt_code": "11014", "quarter": 3,
           "rcept_no": "20251114000001", "report_nm": "분기보고서 (2025.09)",
           "qkey": "2025Q3"}
    result = ingest.ingest_one(FakeDart(), hit, rec)

    assert result["changed"], result
    # 3Q 누계 420 - 반기 누계 250 = 170  (누계를 그대로 쓰면 420이 된다)
    assert approx(rec["quarters"]["2025Q3"]["매출액"], 170 * EOK), rec["quarters"]["2025Q3"]
    # 재무상태표는 이번 분기에만 붙는다
    assert approx(rec["quarters"]["2025Q3"]["지배주주지분"], 900 * EOK)
    assert "지배주주지분" not in rec["quarters"].get("2025Q2", {})
    # 주식수는 그 보고서 시점 값 (과거 시총을 현재 주식수로 계산하면 틀린다)
    assert rec["quarters"]["2025Q3"]["상장주식수"] == 10_000_000
    assert rec["quarters"]["2025Q3"]["rcept_no"] == "20251114000001"
    assert rec["quarters"]["2025Q3"]["fs_div"] == "CFS"
    print("test_ingest_one: OK")


def test_backfill_one_fetches_each_report_once():
    """
    소급 수집은 보고서를 기간마다 한 번씩만 받아야 한다.
    ingest_one 을 반복 호출하면 차분용 직전 분기를 매번 다시 받아 호출이 두 배가 된다.
    """
    import kospi_value_screener as ksv

    seen = []

    class CountingDart(FakeDart):
        def get_json(self, endpoint, params):
            if endpoint == "fnlttSinglAcntAll.json":
                seen.append((params["bsns_year"], params["reprt_code"], params["fs_div"]))
            return FakeDart.get_json(self, endpoint, params)

    periods = ksv.build_period_candidates("20260729", lookback_years=3)
    rec = store.load("222222")
    result = ingest.backfill_one(CountingDart(), "00000001", "소급테스트", periods, rec)

    # CFS 가 바로 성공하므로 보고서 하나당 정확히 한 번
    cfs = [s for s in seen if s[2] == "CFS"]
    assert len(cfs) == len(set(cfs)) == result["fetched"], (len(cfs), len(set(cfs)))
    assert result["fetched"] == len(periods), (result["fetched"], len(periods))

    # 3년치면 분기가 10개는 넘어야 한다
    assert len(rec["quarters"]) >= 10, sorted(rec["quarters"])
    # 누계가 아니라 분기 단독으로 들어갔는지 (3Q누계 420 - 반기누계 250 = 170)
    q3 = [k for k in rec["quarters"] if k.endswith("Q3")][0]
    assert approx(rec["quarters"][q3]["매출액"], 170 * EOK), rec["quarters"][q3]
    # 재무상태표와 주식수가 각 분기에 붙었는지 (한 시점 값을 전 분기에 복사하면 안 된다)
    withbs = [k for k, v in rec["quarters"].items() if v.get("지배주주지분")]
    assert len(withbs) >= 10, len(withbs)
    assert all(rec["quarters"][k].get("상장주식수") == 10_000_000 for k in withbs)
    print(f"test_backfill_one_fetches_each_report_once: OK "
          f"({result['fetched']}건 조회 -> {len(rec['quarters'])}분기)")


def test_fetch_shares_prefers_common():
    got = ingest.fetch_shares(FakeDart(), "00000001", 2025, "11014")
    assert got == 10_000_000, got   # 우선주 100만주를 더하면 안 된다
    print("test_fetch_shares_prefers_common: OK")


def test_price_backtracks_to_business_day():
    closes = {"20251230": 70000.0, "20251229": 69000.0}
    # 12/31 이 휴장이면 직전 영업일로 되감는다
    got = prices.close_on_or_before(closes, dt.date(2025, 12, 31))
    assert got == ("20251230", 70000.0), got
    # 되감기 한도를 넘으면 포기 (엉뚱한 시점 주가를 쓰느니 비우는 게 낫다)
    assert prices.close_on_or_before(closes, dt.date(2026, 3, 31)) is None
    print("test_price_backtracks_to_business_day: OK")


if __name__ == "__main__":
    try:
        test_parse_report_name()
        test_detect_filters_and_dedupes()
        test_merge_does_not_overwrite()
        test_sort_quarters()
        test_save_load_roundtrip_and_trim()
        test_price_snapshot_is_immutable()
        test_quarter_context_uses_that_quarters_price()
        test_timeseries_shape_and_yoy()
        test_site_build()
        test_ingest_one()
        test_backfill_one_fetches_each_report_once()
        test_fetch_shares_prefers_common()
        test_price_backtracks_to_business_day()
        print("\nALL SCREENER PACKAGE TESTS PASSED")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
