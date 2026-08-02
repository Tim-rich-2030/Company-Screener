# -*- coding: utf-8 -*-
"""
시장 신호 계산 테스트 (네트워크 불필요).

지수 시세는 못 받는 환경이 흔하므로, 계산은 시세와 분리해 순수 함수로 두고
여기서 손으로 검산한다.

실행:  python tests/test_market_signal.py
"""
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import market_signal as ms   # noqa: E402


def series(values, start=dt.date(2024, 1, 1)):
    """영업일에 값을 하나씩 붙인 {YYYYMMDD: 종가}."""
    out, d, i = {}, start, 0
    while i < len(values):
        if d.weekday() < 5:
            out[d.strftime("%Y%m%d")] = float(values[i])
            i += 1
        d += dt.timedelta(days=1)
    return out


def approx(a, b, tol=1e-6):
    return a is not None and abs(a - b) < tol


def test_sma_and_disparity():
    """20일선은 마지막 20개의 산술평균, 이격도는 그 대비 %."""
    vals = list(range(1, 41))                    # 1..40
    rows = sorted(series(vals).items())
    # 마지막 20개는 21..40 -> 평균 30.5
    assert approx(ms.sma_at(rows), 30.5)
    # 5일 전 시점의 20일선은 16..35 -> 평균 25.5
    assert approx(ms.sma_at(rows, offset=5), 25.5)

    got = ms.analyse(series(vals), "20991231")
    assert got["close"] == 40.0
    assert approx(got["sma20"], 30.5)
    # (40 - 30.5) / 30.5 * 100 = 31.15%
    assert approx(got["disparity"], round((40 - 30.5) / 30.5 * 100, 2), 0.01)
    print("test_sma_and_disparity: OK")


def test_trend_thresholds():
    """기울기 판정은 상수로 그은 선이다 — 경계에서 어느 쪽인지 못 박아 둔다."""
    assert ms.trend_label(ms.TREND_FLAT_PCT) == "상승"
    assert ms.trend_label(ms.TREND_FLAT_PCT - 0.01) == "횡보"
    assert ms.trend_label(-ms.TREND_FLAT_PCT) == "하락"
    assert ms.trend_label(0.0) == "횡보"

    flat = ms.analyse(series([100.0] * 40), "20991231")
    assert flat["slope_pct"] == 0.0 and flat["trend"] == "횡보"
    rising = ms.analyse(series(list(range(1, 41))), "20991231")
    assert rising["trend"] == "상승"
    falling = ms.analyse(series(list(range(40, 0, -1))), "20991231")
    assert falling["trend"] == "하락"
    print("test_trend_thresholds: OK")


def test_band_position():
    """밴드 위치는 60거래일 저점 0%, 고점 100%."""
    # 마지막 60개가 밴드가 되도록 80개를 만든다
    vals = [100.0] * 20 + list(range(50, 110))   # 뒤 60개: 50..109
    got = ms.analyse(series(vals), "20991231")
    assert got["band_days"] == 60
    assert got["band_high"] == 109.0 and got["band_low"] == 50.0
    assert approx(got["band_pos"], 100.0)        # 종가가 곧 고점

    # 고점에서 절반쯤 내려온 경우
    vals2 = [100.0] * 20 + list(range(50, 109)) + [79.5]
    got2 = ms.analyse(series(vals2), "20991231")
    assert approx(got2["band_pos"], round((79.5 - 50) / (108 - 50) * 100, 1), 0.05)

    # 전 구간이 같은 값이면 위치를 말할 수 없다 — 0%로 단정하면 안 된다
    flat = ms.analyse(series([100.0] * 40), "20991231")
    assert flat["band_pos"] is None
    print("test_band_position: OK")


def test_ratio_leader():
    """코스닥이 더 오르면 비율이 자기 평균 위로 올라간다."""
    kospi = series([100.0] * 40)
    kosdaq = series([100.0] * 20 + [100.0 + i for i in range(1, 21)])
    got = ms.analyse_ratio(kosdaq, kospi, "20991231")
    assert got["disparity"] > 0 and got["leader"] == "코스닥 우위"

    # 반대 방향
    got2 = ms.analyse_ratio(kospi, kosdaq, "20991231")
    assert got2["disparity"] < 0 and got2["leader"] == "코스피 우위"

    # 둘이 나란히 움직이면 비율이 안 변하므로 우열이 없다
    both = series([100.0 + i for i in range(40)])
    got3 = ms.analyse_ratio(both, both, "20991231")
    assert approx(got3["disparity"], 0.0, 1e-9) and got3["leader"] == "비슷"
    print("test_ratio_leader: OK")


def test_as_of_uses_only_past_data():
    """기준일 이후 데이터는 절대 섞이면 안 된다 — 섞이면 미래를 본 값이 된다."""
    vals = list(range(1, 61))
    s = series(vals)
    days = sorted(s)
    cutoff = days[39]                            # 40번째 영업일 = 값 40
    got = ms.analyse(s, cutoff)
    assert got["date"] == cutoff and got["close"] == 40.0
    assert approx(got["sma20"], 30.5)            # 21..40 평균, 41 이후는 안 봄
    print("test_as_of_uses_only_past_data: OK")


def test_compute_clamps_and_reports_both_indices():
    payload = {"series": {"코스피": series(list(range(2000, 2100))),
                          "코스닥": series(list(range(700, 800)))}}
    res = ms.compute(payload)
    assert set(res["indices"]) == {"코스피", "코스닥"}
    assert res["ratio"] is not None
    assert res["params"]["sma"] == ms.SMA_WINDOW
    last = min(max(s) for s in payload["series"].values())
    assert res["as_of"] == last
    # 데이터 끝보다 뒤를 물으면 끝으로 붙인다 (미래를 만들어내지 않는다)
    assert ms.compute(payload, "20991231")["as_of"] == last
    print("test_compute_clamps_and_reports_both_indices: OK")


def test_insufficient_history_is_refused():
    """모자란 데이터로 그럴듯한 숫자를 만들어내면 안 된다."""
    assert ms.analyse(series([100.0] * 10), "20991231") is None
    assert ms.analyse_ratio(series([1.0] * 5), series([1.0] * 5), "20991231") is None
    print("test_insufficient_history_is_refused: OK")


def test_krx_outage_falls_back_instead_of_crashing():
    """
    pykrx 는 import 하는 순간 KRX 로그인을 시도한다. 그래서 KRX 가 죽어 있으면
    import 문에서 예외가 난다 — ImportError 만 잡으면 수집 전체가 트레이스백으로
    죽는다. 지수 시세는 네이버로도 받을 수 있으니 멈출 이유가 없다.
    """
    import builtins
    real_import = builtins.__import__

    def boom(name, *a, **kw):
        if name.startswith("pykrx"):
            raise OSError("KRX 접속 불가 (테스트)")
        return real_import(name, *a, **kw)

    builtins.__import__ = boom
    try:
        got = ms._fetch_pykrx(ms.INDICES["코스피"],
                              dt.date(2026, 1, 1), dt.date(2026, 8, 1))
        assert got == {}, "예외를 삼키고 빈 결과를 돌려줘야 폴백이 돈다"
    finally:
        builtins.__import__ = real_import
    print("test_krx_outage_falls_back_instead_of_crashing: OK")


def test_check_krx_without_credentials_is_not_an_error():
    """계정을 안 넣은 것은 선택이다 — 실행을 실패시키면 안 된다."""
    saved = {k: os.environ.pop(k, None) for k in ("KRX_ID", "KRX_PW")}
    try:
        assert not ms.krx_credentials()
        assert ms.check_krx() == 0
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("test_check_krx_without_credentials_is_not_an_error: OK")


def test_source_label():
    """어디서 받은 값인지 화면·CLI 에 드러나야 계정이 죽은 걸 알아챈다."""
    assert ms.source_label({"코스피": "pykrx", "코스닥": "pykrx"}) == "pykrx"
    assert ms.source_label({"코스피": "pykrx", "코스닥": "naver"}) == "naver+pykrx"
    assert ms.source_label({}) == ""
    assert ms.source_label(None) == ""
    print("test_source_label: OK")


def test_disparity_percentile_beats_minmax_in_a_wide_band():
    """
    60일 min-max 는 최고·최저 딱 두 날이 눈금을 정해서, 밴드가 넓어지면 중간이
    뭉개지고 극단에서는 0%/100% 에 붙는다. 백분위는 분포 전체를 쓴다.
    """
    # 한 번 크게 튀었다가 좁게 움직이는 구간 — min-max 가 망가지는 전형.
    # 꼬리를 55개로 둬야 이상치(index 40)가 마지막 60일 안에 들어온다.
    vals = [100.0] * 40 + [200.0] + [100.0 + (i % 5) for i in range(55)]
    s = series(vals)
    got = ms.analyse(s, "20991231")
    assert got["band_high"] == 200.0, "이상치가 밴드 안에 있어야 하는 테스트다"
    # 이상치 하나가 밴드 상단을 200 으로 밀어 올려 위치가 바닥에 눌린다
    assert got["band_pos"] < 10
    # 백분위는 같은 상황에서도 등급이 살아 있다
    assert got["stretch_pct"] is not None
    assert 0 < got["stretch_pct"] < 100
    print("test_disparity_percentile_beats_minmax_in_a_wide_band: OK")


def test_percentile_extremes_and_bounds():
    """가장 눌린 날은 0 에 가깝고 가장 뜬 날은 100 에 가까워야 한다."""
    # 조용하다가 마지막에 급등 -> 오늘이 1년 중 가장 뜬 날
    spike = ms.analyse(series([100.0] * 200 + [130.0]), "20991231")
    assert spike["stretch_pct"] == 100.0
    # 조용하다가 급락 -> 가장 눌린 날
    crash = ms.analyse(series([100.0] * 200 + [70.0]), "20991231")
    assert crash["stretch_pct"] < 1.0
    for r in (spike, crash):
        assert 0 <= r["stretch_pct"] <= 100

    # 등차로 오르기만 하면 이격도는 오히려 줄어든다(격차는 일정한데 분모가 커진다).
    # 백분위는 '주가가 올랐나'가 아니라 '평균에서 얼마나 벌어졌나'를 재는 값이다.
    ramp = ms.analyse(series(list(range(1, 200))), "20991231")
    assert ramp["disparity"] > 0 and ramp["stretch_pct"] < 5
    # 이력이 모자라면 만들어내지 않는다
    short = ms.analyse(series([100.0 + i for i in range(30)]), "20991231")
    assert short["stretch_pct"] is None
    print("test_percentile_extremes_and_bounds: OK")


if __name__ == "__main__":
    test_sma_and_disparity()
    test_trend_thresholds()
    test_band_position()
    test_ratio_leader()
    test_as_of_uses_only_past_data()
    test_compute_clamps_and_reports_both_indices()
    test_insufficient_history_is_refused()
    test_krx_outage_falls_back_instead_of_crashing()
    test_check_krx_without_credentials_is_not_an_error()
    test_source_label()
    test_disparity_percentile_beats_minmax_in_a_wide_band()
    test_percentile_extremes_and_bounds()
    print("\nALL MARKET SIGNAL TESTS PASSED")
