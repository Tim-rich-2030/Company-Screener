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


def test_meta_attaches_to_already_filled_quarter():
    """
    통화처럼 나중에 생긴 메타는, 값이 이미 다 찬 분기에도 붙어야 한다.
    changed 일 때만 붙이던 시절 두산밥캣이 USD 공시인데 통화가 비어
    PBR 이 1,000배로 찍혔다.
    """
    rec = {"code": "241560", "name": "두산밥캣", "quarters": {}}
    store.merge_quarter(rec, "2025Q4", {"매출액": 100.0}, {"fs_div": "CFS"})
    # 새로 채울 값은 없지만(= changed False) 통화는 달라붙어야 한다
    assert not store.merge_quarter(rec, "2025Q4", {"매출액": 999.0},
                                   {"currency": "USD"})
    assert rec["quarters"]["2025Q4"]["currency"] == "USD"
    assert rec["quarters"]["2025Q4"]["매출액"] == 100.0
    # 빈 문자열은 붙이지 않는다 — 나중에 진짜 값이 오는 걸 막으면 안 된다
    rec2 = {"code": "005930", "quarters": {}}
    store.merge_quarter(rec2, "2025Q4", {"매출액": 1.0}, {"currency": ""})
    assert "currency" not in rec2["quarters"]["2025Q4"]
    print("test_meta_attaches_to_already_filled_quarter: OK")


def _shares_record(pairs):
    return {"code": "000000", "quarters":
            {q: {"상장주식수": v, "종가": 1000.0} for q, v in pairs}}


def test_implausible_share_spike_is_dropped_but_real_split_kept():
    """
    한 분기만 자릿수가 튀는 값은 공시 오류, 한쪽 이웃과 맞으면 진짜 자본 변동이다.
    실데이터에서 나온 두 경우를 그대로 재현한다.
    """
    # LS에코에너지 2025Q4 — 정확히 100만 배로 솟았다가 다음 분기에 되돌아온다
    rec = _shares_record([("2025Q2", 30624879.0), ("2025Q3", 30624879.0),
                          ("2025Q4", 30624879000000.0), ("2026Q1", 30624879.0)])
    assert store.shares_look_wrong(rec, "2025Q4", 30624879000000.0)
    assert store.drop_implausible_shares(rec) == ["2025Q4"]
    assert "상장주식수" not in rec["quarters"]["2025Q4"]
    # 지우고 나면 이웃 값을 이어받아 시가총액까지 복구된다
    store.fill_missing_shares(rec)
    assert rec["quarters"]["2025Q4"]["상장주식수"] == 30624879.0
    assert approx(rec["quarters"]["2025Q4"]["시가총액"], 30624879.0 * 1000.0)

    # 카프로 2024Q2 — 증자로 4천만 -> 1억6900만. 계단이므로 건드리면 안 된다
    real = _shares_record([("2024Q1", 40000000.0), ("2024Q2", 168999996.0),
                           ("2024Q3", 168999996.0), ("2024Q4", 168999996.0)])
    assert not store.shares_look_wrong(real, "2024Q2", 168999996.0)
    assert store.drop_implausible_shares(real) == []

    # 20배를 넘는 액면분할이라도 한쪽 이웃과 맞으면 살려둔다
    split = _shares_record([("2024Q1", 1000000.0), ("2024Q2", 50000000.0),
                            ("2024Q3", 50000000.0)])
    assert store.drop_implausible_shares(split) == []

    # 가장 오래된/최근 분기는 이웃이 한쪽뿐이라 판단 근거가 약하다. 여기서 잘못
    # 지우면 이웃 값을 끌어와 채우므로 틀린 값이 된다 — 그래서 기준을 크게 잡는다.
    edge_split = _shares_record([("2024Q1", 1000000.0), ("2024Q2", 50000000.0)])
    assert store.drop_implausible_shares(edge_split) == []   # 50:1 분할은 살린다
    # 분기가 둘뿐인데 서로 100만 배 어긋나면 어느 쪽이 맞는지 알 길이 없다.
    # 둘 다 버려 값을 비운다 — 반쪽을 믿고 남기면 100만 배 틀린 PBR 이 나온다.
    edge_bad = _shares_record([("2025Q3", 30624879.0),
                               ("2025Q4", 30624879000000.0)])
    assert store.drop_implausible_shares(edge_bad) == ["2025Q3", "2025Q4"]
    print("test_implausible_share_spike_is_dropped_but_real_split_kept: OK")


def test_set_shares_refuses_implausible_value():
    """다시 받아온 값이 여전히 틀렸으면 저장하지 않는다. 없는 편이 낫다."""
    rec = _shares_record([("2025Q2", 30624879.0), ("2025Q3", 30624879.0),
                          ("2026Q1", 30624879.0)])
    rec["quarters"]["2025Q4"] = {"종가": 1000.0}
    assert not store.set_shares(rec, "2025Q4", 30624879000000.0)
    assert rec["quarters"]["2025Q4"].get("상장주식수") is None
    # 멀쩡한 값은 그대로 받는다
    assert store.set_shares(rec, "2025Q4", 30624879.0)
    assert rec["quarters"]["2025Q4"]["상장주식수"] == 30624879.0
    # 견줄 이웃이 없으면 판단하지 않고 받는다
    lone = {"code": "000000", "quarters": {"2025Q4": {"종가": 1000.0}}}
    assert store.set_shares(lone, "2025Q4", 12345.0)
    print("test_set_shares_refuses_implausible_value: OK")


def test_frozen_price_marks_halted_ticker():
    """
    거래정지 종목은 마지막 체결가가 그대로 남아 분기말 종가가 안 움직인다.
    실데이터의 카프로(9분기 3,660원)·금양(5분기 9,900원)이 그 경우다.
    """
    def rec(closes):
        return {"code": "006380", "quarters":
                {q: {"종가": c} for q, c in closes}}

    halted = rec([("2025Q2", 3660.0), ("2025Q3", 3660.0),
                  ("2025Q4", 3660.0), ("2026Q1", 3660.0)])
    got = store.frozen_price_run(halted)
    assert got == {"quarters": 4, "close": 3660.0, "since": "2025Q2"}

    # 거래가 살아 있으면 잡히지 않는다
    live = rec([("2025Q3", 3600.0), ("2025Q4", 4280.0), ("2026Q1", 3660.0)])
    assert store.frozen_price_run(live) is None

    # 최신 분기부터 이어진 구간만 센다. 과거에 같은 값이 있어도 끊기면 거기까지다.
    resumed = rec([("2025Q1", 3660.0), ("2025Q2", 3660.0),
                   ("2025Q3", 4000.0), ("2025Q4", 4100.0), ("2026Q1", 4100.0)])
    assert store.frozen_price_run(resumed)["quarters"] == 2
    assert store.frozen_price_run(resumed)["close"] == 4100.0

    # 한 분기만 같으면(= 비교 대상이 하나뿐) 판단하지 않는다
    assert store.frozen_price_run(rec([("2026Q1", 3660.0)])) is None
    # 기준 분기 수를 올리면 짧은 구간은 빠진다
    assert store.frozen_price_run(halted, min_quarters=5) is None
    print("test_frozen_price_marks_halted_ticker: OK")


def test_site_marks_halted_company():
    """정지 종목은 목록과 상세 양쪽에 표시돼야 한다."""
    tmp = tempfile.mkdtemp()
    try:
        rec = {"code": "006380", "name": "카프로", "quarters": {
            q: {"종가": 3660.0, "상장주식수": 168999996.0,
                "시가총액": 3660.0 * 168999996.0, "자본총계": 7.0e10,
                "지배주주지분": 7.0e10, "매출액": 1.0e10, "영업이익": 1.0e9,
                "순이익": 1.0e9, "지배주주순이익": 1.0e9, "자산총계": 2.0e11,
                "부채총계": 1.3e11}
            for q in ("2025Q2", "2025Q3", "2025Q4", "2026Q1")}}
        old_facts, old_site = cfg.FACTS_DIR, cfg.SITE_DIR
        cfg.FACTS_DIR = os.path.join(tmp, "facts")
        cfg.SITE_DIR = os.path.join(tmp, "docs")
        os.makedirs(cfg.FACTS_DIR, exist_ok=True)
        store.save(rec)
        path = site.build(cfg.SITE_DIR)
        page = open(path, encoding="utf-8").read()
        assert '"halted": true' in page or '"halted":true' in page
        assert "거래정지 의심" in page      # 상세 배지
        assert 'class="halt"' in page       # 목록 표시
        # 근거를 화면에 같이 띄운다 — 단정이 아니라 추정이므로
        assert "4개 분기 연속" in page or "c.halted.quarters" in page
    finally:
        cfg.FACTS_DIR, cfg.SITE_DIR = old_facts, old_site
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_site_marks_halted_company: OK")


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


def test_site_shows_backfill_progress():
    """
    소급이 어디까지 왔는지 화면에 띄운다. 안 띄우면 저장소 파일을 세어 보는
    수밖에 없어서, 다 모였는지 알 방법이 없다.
    """
    store.save(sample_record())
    saved = len([r for r in store.load_all() if r.get("quarters")])

    st = store.load_state()
    st["backfill_total"] = saved + 200        # 아직 한참 남은 상태
    store.save_state(st)
    doc = open(site.build(cfg.SITE_DIR), encoding="utf-8").read()
    assert f"{saved} / {saved + 200}종목" in doc, "진행률이 안 보입니다"
    assert "%)" in doc

    st = store.load_state()
    st["backfill_total"] = saved              # 목표에 도달
    store.save_state(st)
    doc = open(site.build(cfg.SITE_DIR), encoding="utf-8").read()
    assert "과거 수집 완료" in doc, "완료 표시가 안 보입니다"

    # 마지막 실행 시각도 함께 보여야 "돌고 있나"를 사이트만 보고 알 수 있다
    st = store.load_state()
    st["backfill_last_run"] = "2026-07-30T03:55:00Z"
    st["last_run"] = "2026-07-30T01:03:06Z"
    store.save_state(st)
    doc = open(site.build(cfg.SITE_DIR), encoding="utf-8").read()
    assert "소급 마지막 실행" in doc and "2026-07-30T03:55:00Z" in doc, "실행 시각이 없습니다"
    assert "공시 감지 마지막 실행" in doc
    assert "time[datetime]" in doc, "보는 사람 시간대로 바꾸는 스크립트가 없습니다"

    st = store.load_state()
    st.pop("backfill_total", None)    # 소급을 한 번도 안 돌린 경우
    store.save_state(st)
    doc = open(site.build(cfg.SITE_DIR), encoding="utf-8").read()
    assert "과거 수집" not in doc
    print("test_site_shows_backfill_progress: OK")


# =============================================================================
# 5) 수집 (DART 응답 흉내)
# =============================================================================

class FakeDart:
    calls = 0
    last_error = ""

    def get_json(self, endpoint, params):
        if endpoint == "stockTotqySttus.json":
            # 실제 응답 구조. isu_stock_totqy 는 정관상 '발행할' 주식의 총수(수권주식수)라
            # 실제 발행주식수보다 훨씬 크다. 이걸 쓰면 시가총액이 통째로 부풀려진다.
            return {"status": "000", "list": [
                {"se": "보통주", "isu_stock_totqy": "500,000,000",
                 "istc_totqy": "10,000,000", "distb_stock_co": "9,500,000"},
                {"se": "우선주", "isu_stock_totqy": "50,000,000",
                 "istc_totqy": "1,000,000", "distb_stock_co": "1,000,000"}]}
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


def test_fetch_shares_uses_issued_not_authorized():
    """
    주식총수현황의 필드 이름이 헷갈린다.
      isu_stock_totqy  발행'할' 주식의 총수 = 정관상 수권주식수 (실제보다 훨씬 큼)
      istc_totqy       발행주식의 총수     = 실제 발행 수  <- 이걸 써야 한다
    수권주식수를 쓰면 시가총액이 부풀려져 PBR·PER 이 통째로 틀린다.
    """
    got = ingest.fetch_shares(FakeDart(), "00000001", 2025, "11014")
    assert got == 10_000_000, got          # istc_totqy
    assert got != 500_000_000, "수권주식수(발행할 주식의 총수)를 쓰고 있습니다"
    assert got != 11_000_000, "우선주를 더하면 안 됩니다"

    # istc_totqy 가 없으면 유통주식수로 대체
    class NoIssued(FakeDart):
        def get_json(self, endpoint, params):
            if endpoint == "stockTotqySttus.json":
                return {"status": "000", "list": [
                    {"se": "보통주", "isu_stock_totqy": "500,000,000",
                     "distb_stock_co": "9,500,000"}]}
            return FakeDart.get_json(self, endpoint, params)
    assert ingest.fetch_shares(NoIssued(), "00000001", 2025, "11014") == 9_500_000
    print("test_fetch_shares_uses_issued_not_authorized: OK")


def test_missing_shares_carried_from_neighbour():
    """
    분기보고서에 주식총수현황이 없는 경우가 있다. 그대로 두면 그 분기 PBR·PER 이
    통째로 비므로 인접 분기 값을 이어받는다. 오늘 주식수를 쓰는 것보다 정확하다.
    """
    rec = {"code": "777777", "name": "주식수테스트", "quarters": {}}
    for q in ("2025Q1", "2025Q2", "2025Q3", "2025Q4"):
        store.merge_quarter(rec, q, {"매출액": 100.0})
        store.set_price(rec, q, 1000.0, 0, "20250101", "test")
    rec["quarters"]["2025Q2"]["상장주식수"] = 5_000_000

    filled = store.fill_missing_shares(rec)
    assert filled == 3, filled
    assert rec["quarters"]["2025Q3"]["상장주식수"] == 5_000_000   # 직전에서 이어받음
    assert rec["quarters"]["2025Q1"]["상장주식수"] == 5_000_000   # 가장 오래된 구간은 거꾸로
    assert rec["quarters"]["2025Q1"]["shares_src"] == "carried-back"
    assert rec["quarters"]["2025Q3"]["shares_src"] == "carried"
    # 시가총액이 다시 계산되어야 한다
    assert rec["quarters"]["2025Q3"]["시가총액"] == 1000.0 * 5_000_000
    print("test_missing_shares_carried_from_neighbour: OK")


def test_missing_mcap_yields_blank_not_zero():
    """
    시가총액이 없을 때 PBR 이 0.00 으로 찍히면 '계산된 값'처럼 보인다.
    비어 있어야 정렬에서도 뒤로 가고 오해가 없다.
    """
    rec = sample_record()
    for slot in rec["quarters"].values():
        slot["시가총액"] = None
    _, _, rows = None, None, None
    ts = compute_timeseries(rec, qd.METRICS)
    assert all(v is None for v in ts["metrics"]["PBR"]), ts["metrics"]["PBR"][:3]
    assert all(v is None for v in ts["metrics"]["PER"]), ts["metrics"]["PER"][:3]
    # 주가와 무관한 지표는 계속 나와야 한다
    assert ts["metrics"]["ROE(%)"][0] is not None
    print("test_missing_mcap_yields_blank_not_zero: OK")


def test_foreign_currency_blanks_price_ratios():
    """
    두산밥캣처럼 재무제표를 USD 로 내는 회사가 있다. 달러 자기자본을 원화
    시가총액으로 나누면 PBR 이 1,000배로 나온다 (실제로 그렇게 나왔다).
    통화가 원화가 아니면 시총 기반 지표는 계산하지 않고, 통화와 무관한
    비율(ROE·영업이익률)은 그대로 살린다.
    """
    rec = sample_record()
    for slot in rec["quarters"].values():
        slot["currency"] = "USD"
    ts = compute_timeseries(rec, qd.METRICS)
    assert all(v is None for v in ts["metrics"]["PBR"]), ts["metrics"]["PBR"][:3]
    assert all(v is None for v in ts["metrics"]["PER"])
    assert ts["metrics"]["ROE(%)"][0] is not None, "통화와 무관한 비율까지 지우면 안 됩니다"
    assert ts["metrics"]["분기 영업이익률(%)"][0] is not None

    # 원화면 그대로 계산된다
    for slot in rec["quarters"].values():
        slot["currency"] = "KRW"
    assert compute_timeseries(rec, qd.METRICS)["metrics"]["PBR"][0] is not None
    # currency 가 아예 없으면 원화로 본다 (예전에 수집한 데이터)
    for slot in rec["quarters"].values():
        slot.pop("currency", None)
    assert compute_timeseries(rec, qd.METRICS)["metrics"]["PBR"][0] is not None
    print("test_foreign_currency_blanks_price_ratios: OK")


def test_detect_currency():
    assert ingest.detect_currency({"list": [{"currency": "USD"}]}) == "USD"
    assert ingest.detect_currency({"list": [{"currency": " krw "}]}) == "KRW"
    assert ingest.detect_currency({"list": [{}]}) == ""
    assert ingest.detect_currency({}) == ""
    print("test_detect_currency: OK")


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
        test_meta_attaches_to_already_filled_quarter()
        test_implausible_share_spike_is_dropped_but_real_split_kept()
        test_set_shares_refuses_implausible_value()
        test_frozen_price_marks_halted_ticker()
        test_site_marks_halted_company()
        test_sort_quarters()
        test_save_load_roundtrip_and_trim()
        test_price_snapshot_is_immutable()
        test_quarter_context_uses_that_quarters_price()
        test_timeseries_shape_and_yoy()
        test_site_build()
        test_site_shows_backfill_progress()
        test_ingest_one()
        test_backfill_one_fetches_each_report_once()
        test_fetch_shares_uses_issued_not_authorized()
        test_missing_shares_carried_from_neighbour()
        test_missing_mcap_yields_blank_not_zero()
        test_foreign_currency_blanks_price_ratios()
        test_detect_currency()
        test_price_backtracks_to_business_day()
        print("\nALL SCREENER PACKAGE TESTS PASSED")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
