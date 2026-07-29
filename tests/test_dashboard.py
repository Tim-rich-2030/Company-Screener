"""
분기 대시보드 테스트 (네트워크 불필요).

핵심은 두 가지다.
  1) 누계 -> 분기 단독 환산이 맞는가 (여기가 틀리면 모든 지표가 틀린다)
  2) 지표를 새로 등록하면 열이 실제로 늘어나는가 (요구사항의 핵심)

실행:  python tests/test_dashboard.py
"""
import os
import re
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import quarterly_dashboard as qd

EOK = 100_000_000


def approx(a, b, tol=1e-6):
    return a is not None and abs(float(a) - float(b)) < tol


# =============================================================================
# 1) 누계 -> 분기 단독 환산
# =============================================================================

def test_cumulative_to_quarterly():
    # 매출 누계: 1Q 100, 반기 250, 3Q 420, 연간 600
    # => 분기 단독: Q1 100, Q2 150, Q3 170, Q4 180
    cums = {
        (2025, 1): {"매출액": 100.0},
        (2025, 2): {"매출액": 250.0},
        (2025, 3): {"매출액": 420.0},
        (2025, 4): {"매출액": 600.0},
    }
    q = qd.cumulative_to_quarterly(cums)
    assert approx(q[(2025, 1)]["매출액"], 100)
    assert approx(q[(2025, 2)]["매출액"], 150)
    assert approx(q[(2025, 3)]["매출액"], 170)
    assert approx(q[(2025, 4)]["매출액"], 180)

    # 적자 분기도 부호가 보존되어야 한다 (3Q 누계가 반기보다 작은 경우)
    loss = {(2024, 1): {"영업이익": 50.0}, (2024, 2): {"영업이익": 20.0}}
    assert approx(qd.cumulative_to_quarterly(loss)[(2024, 2)]["영업이익"], -30)

    # 직전 분기 누계가 없으면 분기 단독을 만들 수 없다 -> 아예 넣지 않는다
    holey = {(2023, 3): {"매출액": 900.0}}
    assert (2023, 3) not in qd.cumulative_to_quarterly(holey)
    print("test_cumulative_to_quarterly: OK")


def test_parse_cumulative_is():
    """당기 누계와 전기(전년 동기) 누계를 한 응답에서 모두 뽑는다."""
    payload = {"status": "000", "list": [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "80", "thstrm_add_amount": "250",
         "frmtrm_amount": "70", "frmtrm_add_amount": "200"},
        {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
         "thstrm_amount": "8", "thstrm_add_amount": "25",
         "frmtrm_amount": "-7", "frmtrm_add_amount": "-20"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
         "thstrm_amount": "9,999"},
    ]}
    got = qd.parse_cumulative_is(payload, 2025, 2)
    # 누계(add)를 쓴다. 3개월치(amount)를 쓰면 안 된다.
    assert approx(got[(2025, 2)]["매출액"], 250), got
    assert approx(got[(2024, 2)]["매출액"], 200), got
    assert approx(got[(2024, 2)]["영업이익"], -20), got
    assert "자본총계" not in got[(2025, 2)], "재무상태표 항목이 손익에 섞였습니다"

    # 누계 칸이 비면 amount로 대체한다 (사업보고서에는 add_amount가 없다)
    annual = {"status": "000", "list": [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "600", "frmtrm_amount": "500"}]}
    got2 = qd.parse_cumulative_is(annual, 2025, 4)
    assert approx(got2[(2025, 4)]["매출액"], 600)
    assert approx(got2[(2024, 4)]["매출액"], 500)

    assert qd.parse_cumulative_is({"status": "013"}, 2025, 1) == {}
    print("test_parse_cumulative_is: OK")


# =============================================================================
# 2) 지표 계산
# =============================================================================

def sample_data():
    """매 분기 매출 100·영업이익 10·순이익 8, 자기자본 1,000억인 회사."""
    quarters = {}
    for i, (y, q) in enumerate([(2026, 1), (2025, 4), (2025, 3), (2025, 2),
                                (2025, 1), (2024, 4), (2024, 3), (2024, 2)]):
        quarters[f"{y}Q{q}"] = {
            "매출액": 100.0 * EOK,
            "영업이익": 10.0 * EOK,
            "순이익": 8.0 * EOK,
            "지배주주순이익": 8.0 * EOK,
        }
    return {
        "base_date": "20260729",
        "collected_reports": ["2026 1Q", "2025 FY"],
        "records": [{
            "code": "000001", "name": "테스트전자",
            "price": 10_000.0, "mcap": 2_000.0 * EOK, "shares": 20_000_000.0,
            "quarters": quarters,
            "balance": {"자본총계": 1_100.0 * EOK, "지배주주지분": 1_000.0 * EOK,
                        "자산총계": 3_000.0 * EOK, "부채총계": 2_000.0 * EOK},
            "balance_period": "2026 1Q", "fs_div": "연결", "notes": "",
        }],
    }


def test_builtin_metrics():
    data = sample_data()
    metrics, labels, rows = qd.build_table(data)
    v = rows[0]["metrics"]

    # 자기자본은 자본총계(1,100억)가 아니라 지배주주지분(1,000억)
    assert approx(v["PBR"], 2.0), v["PBR"]          # 2,000억 / 1,000억
    # TTM 순이익 = 8억 × 4분기 = 32억 -> PER = 2,000 / 32
    assert approx(v["PER"], 62.5), v["PER"]
    assert approx(v["ROE(%)"], 3.2), v["ROE(%)"]    # 32억 / 1,000억
    assert approx(v["영업이익률(%)"], 10.0), v["영업이익률(%)"]
    assert approx(v["매출성장률(%)"], 0.0), v["매출성장률(%)"]
    assert approx(v["영업흑자 분기"], 8), v["영업흑자 분기"]
    assert approx(v["부채비율(%)"], 200.0), v["부채비율(%)"]
    assert len(labels) == 8, labels
    print("test_builtin_metrics: OK")


def test_metric_returns_none_when_data_missing():
    """분기가 모자라거나 적자면 계산하지 않고 비운다 (0으로 채우면 정렬이 망가진다)."""
    data = sample_data()
    rec = data["records"][0]
    # 최근 4분기 중 하나를 지우면 TTM 계열은 전부 None
    del rec["quarters"]["2025Q4"]["지배주주순이익"]
    del rec["quarters"]["2025Q4"]["순이익"]
    _, _, rows = qd.build_table(data)
    assert rows[0]["metrics"]["PER"] is None
    assert rows[0]["metrics"]["ROE(%)"] is None
    assert rows[0]["metrics"]["PBR"] is not None, "PBR은 분기 데이터와 무관해야 합니다"

    # 적자면 PER을 내지 않는다
    data2 = sample_data()
    for q in data2["records"][0]["quarters"].values():
        q["지배주주순이익"] = -1.0 * EOK
        q["순이익"] = -1.0 * EOK
    _, _, rows2 = qd.build_table(data2)
    assert rows2[0]["metrics"]["PER"] is None
    assert rows2[0]["metrics"]["ROE(%)"] < 0
    print("test_metric_returns_none_when_data_missing: OK")


def test_yoy_uses_same_quarter_last_year():
    data = sample_data()
    rec = data["records"][0]
    rec["quarters"]["2026Q1"]["영업이익"] = 15.0 * EOK   # 전년 동기(2025Q1)는 10억
    _, labels, rows = qd.build_table(data)
    ctx = qd.MetricContext(rec, labels)
    assert labels[0] == "2026Q1" and labels[4] == "2025Q1", labels
    assert approx(ctx.yoy("영업이익"), 50.0)
    assert approx(rows[0]["metrics"]["영업이익 YoY(%)"], 50.0)
    print("test_yoy_uses_same_quarter_last_year: OK")


# =============================================================================
# 3) 요구사항의 핵심 — 지표를 추가하면 열이 늘어나는가
# =============================================================================

def test_registering_metric_adds_column():
    data = sample_data()
    before = len(qd.build_table(data)[0])

    @qd.metric("내지표", desc="테스트용", fmt="{:.1f}", better="high")
    def my_metric(c):
        return c.mcap / EOK / 100

    try:
        metrics, _, rows = qd.build_table(data)
        assert len(metrics) == before + 1
        assert "내지표" in rows[0]["metrics"]
        assert approx(rows[0]["metrics"]["내지표"], 20.0)

        # 지표가 예외를 던져도 그 종목만 비고 전체가 죽지 않아야 한다
        @qd.metric("터지는지표")
        def boom(c):
            raise ValueError("의도된 오류")

        _, _, rows2 = qd.build_table(data)
        assert rows2[0]["metrics"]["터지는지표"] is None
    finally:
        qd.METRICS.pop("내지표", None)
        qd.METRICS.pop("터지는지표", None)
    print("test_registering_metric_adds_column: OK")


def test_custom_metrics_file_loads():
    """metrics_custom.py 의 예시 지표들이 실제로 등록되는지."""
    qd.load_custom_metrics()
    for key in ("PSR", "PBR/ROE", "영업이익 연속증가"):
        assert key in qd.METRICS, f"{key} 가 등록되지 않았습니다"
    data = sample_data()
    _, _, rows = qd.build_table(data)
    # PSR = 2,000억 / (100억 × 4분기) = 5.0
    assert approx(rows[0]["metrics"]["PSR"], 5.0), rows[0]["metrics"]["PSR"]
    print("test_custom_metrics_file_loads: OK")


# =============================================================================
# 4) 렌더
# =============================================================================

def test_render_html():
    data = sample_data()
    workdir = tempfile.mkdtemp(prefix="dash_")
    prev = os.getcwd()
    try:
        os.chdir(workdir)
        path = qd.render_html(data, "out.html")
        doc = open(path, encoding="utf-8").read()

        # 자체 완결형이어야 한다 — 외부 리소스를 부르면 안 된다
        assert "http://" not in doc.replace("http://www.w3.org", "")
        assert "https://" not in doc
        assert "<script src" not in doc and "<link" not in doc

        assert "테스트전자" in doc and "000001" in doc
        for key in ("PBR", "PER", "ROE(%)"):
            assert key in doc, key
        # 데이터가 JSON으로 박혀 있어야 정렬·검색이 동작한다
        assert '"rows"' in doc and '"metrics"' in doc
        assert re.search(r'<title>.*대시보드.*</title>', doc)
        # 라이트/다크 양쪽 대응
        assert "prefers-color-scheme" in doc and "data-theme" in doc
        print("test_render_html: OK")
    finally:
        os.chdir(prev)
        shutil.rmtree(workdir, ignore_errors=True)


def test_collect_output_shape():
    """collect_one 이 만드는 레코드 구조가 지표 계층이 기대하는 모양인지."""
    class FakeClient:
        calls = 0
        last_error = ""

        def get_json(self, endpoint, params):
            year, code = int(params["bsns_year"]), params["reprt_code"]
            if params["fs_div"] != "CFS":
                return {"status": "013"}
            q = qd.QUARTER_OF_REPRT[code]
            cum = {1: 100, 2: 250, 3: 420, 4: 600}[q]
            return {"status": "000", "list": [
                {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
                 "thstrm_add_amount": str(cum), "frmtrm_add_amount": str(cum - 10)},
                {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
                 "thstrm_amount": "1000"},
                {"sj_div": "BS", "account_id": "ifrs-full_Liabilities",
                 "account_nm": "부채총계", "thstrm_amount": "2000"},
            ]}

    periods = qd.build_period_candidates("20260729")[:qd.REPORTS_PER_TICKER]
    rec = qd.collect_one(FakeClient(), "00126380", periods)
    assert rec["fs_div"] == "연결"
    assert rec["balance"]["자본총계"] == 1000
    assert rec["balance"]["부채총계"] == 2000
    assert rec["balance_period"] == periods[0].label
    # 분기 라벨이 "YYYYQn" 형태로 나오는지
    assert all(re.fullmatch(r"\d{4}Q[1-4]", k) for k in rec["quarters"]), rec["quarters"]
    assert rec["quarters"], "분기 데이터가 비었습니다"
    print("test_collect_output_shape: OK")


if __name__ == "__main__":
    test_cumulative_to_quarterly()
    test_parse_cumulative_is()
    test_builtin_metrics()
    test_metric_returns_none_when_data_missing()
    test_yoy_uses_same_quarter_last_year()
    test_registering_metric_adds_column()
    test_custom_metrics_file_loads()
    test_render_html()
    test_collect_output_shape()
    print("\nALL DASHBOARD TESTS PASSED")
