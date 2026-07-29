"""
파이프라인 통합 테스트 (네트워크 불필요).

pykrx와 DART HTTP 계층만 실제 응답 형태로 흉내내고, run()을 처음부터 끝까지
돌려 CSV 산출물까지 확인한다. 실제로 검증되지 않는 것은 '네트워크 왕복' 하나뿐이다.

실행:  python tests/test_pipeline.py
"""
import io
import os
import sys
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import kospi_value_screener as k

FAKE_KEY = "TEST_KEY_NOT_A_REAL_DART_KEY"
BASE_DATE = "20260728"          # -> 최신 보고서 후보는 "2026 1Q"
JO = 1_000_000_000_000          # 1조


# =============================================================================
# 픽스처: pykrx 시장 데이터
# =============================================================================
# (종목코드, 종목명, 종가, 시가총액, 상장주식수, 시장PBR)
MARKET_ROWS = [
    ("005930", "삼성전자",   70_000, 600 * JO, 5_969_782_550, 1.45),  # 계산PBR 1.5 -> PBR필터
    ("000270", "기아",      105_000,  42 * JO,   400_000_000, 0.75),  # 계산PBR 0.7 -> 통과
    ("005380", "현대차",    200_000,  40 * JO,   200_000_000, 0.55),  # 계산PBR 0.5 -> 통과(우선주 Y)
    ("005387", "현대차2우B", 150_000,   6 * JO,    40_000_000, 0.40),  # 우선주 자체 -> 제외
    ("333333", "별도만공시", 40_000,   4 * JO,   100_000_000, 0.45),  # OFS만 존재 -> 통과
    ("222222", "자본잠식사", 1_000,    1 * JO,   100_000_000, 0.00),  # 자기자본 음수 -> 제외
    ("111111", "보고서없음", 5_000,    1 * JO,   100_000_000, 0.60),  # DART 013 -> 제외
    ("444444", "소형주",     3_000, 300_000_000_000, 50_000_000, 0.30),  # 3,000억 -> 시총필터
    ("555555", "미등록사",   9_000,    1 * JO,   100_000_000, 0.90),  # corpCode 없음 -> 제외
]

CORP_CODE = {code: f"{i:08d}" for i, (code, *_) in enumerate(MARKET_ROWS, start=100)}
del CORP_CODE["555555"]         # corpCode 목록에 없는 종목 (신규 상장 등)


def fake_market_cap(date, market="KOSPI"):
    df = pd.DataFrame(
        [(c, price, cap, cap // max(price, 1), shares)
         for c, _n, price, cap, shares, _p in MARKET_ROWS],
        columns=["티커", "종가", "시가총액", "거래대금", "상장주식수"],
    ).set_index("티커")
    df["거래량"] = 1000
    return df[["종가", "시가총액", "거래량", "거래대금", "상장주식수"]]


def fake_fundamental(date, market="KOSPI"):
    df = pd.DataFrame(
        [(c, pbr) for c, _n, _pr, _cap, _s, pbr in MARKET_ROWS],
        columns=["티커", "PBR"],
    ).set_index("티커")
    for col in ("BPS", "PER", "EPS", "DIV", "DPS"):
        df[col] = 0.0
    return df[["BPS", "PER", "PBR", "EPS", "DIV", "DPS"]]


def fake_ticker_name(code):
    return next(n for c, n, *_ in MARKET_ROWS if c == code)


# =============================================================================
# 픽스처: DART 응답
# =============================================================================

def bs_item(account_id, account_nm, amount, ord_=1):
    """fnlttSinglAcntAll 응답 항목 (실제 필드 구성과 동일)."""
    return {
        "rcept_no": "20260515000001", "reprt_code": "11013", "bsns_year": "2026",
        "corp_code": "00000000", "sj_div": "BS", "sj_nm": "재무상태표",
        "account_id": account_id, "account_nm": account_nm, "account_detail": "-",
        "thstrm_nm": "제 58 기 1분기말", "thstrm_amount": f"{amount:,}",
        "frmtrm_nm": "제 57 기말", "frmtrm_amount": f"{amount:,}",
        "ord": str(ord_), "currency": "KRW",
    }


def bs_payload(equity_total=None, parent_equity=None, noncontrolling=None):
    items = [
        bs_item("ifrs-full_Assets", "자산총계", 999 * JO, 1),
        bs_item("ifrs-full_Liabilities", "부채총계", 111 * JO, 2),
    ]
    if parent_equity is not None:
        items.append(bs_item("ifrs-full_EquityAttributableToOwnersOfParent",
                             "지배기업의 소유주에게 귀속되는 자본", parent_equity, 3))
    if noncontrolling is not None:
        items.append(bs_item("ifrs-full_NoncontrollingInterests", "비지배지분",
                             noncontrolling, 4))
    if equity_total is not None:
        items.append(bs_item("ifrs-full_Equity", "자본총계", equity_total, 5))
    # 손익계산서 항목이 섞여 있어도 무시되는지 확인
    items.append({"sj_div": "IS", "sj_nm": "손익계산서", "account_id": "ifrs-full_Revenue",
                  "account_nm": "매출액", "thstrm_amount": "1,000", "ord": "1"})
    return {"status": "000", "message": "정상", "list": items}


NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}

# (corp_code, bsns_year, reprt_code, fs_div) -> payload
DART_FIXTURES = {
    (CORP_CODE["005930"], "2026", "11013", "CFS"):
        bs_payload(equity_total=405 * JO, parent_equity=400 * JO, noncontrolling=5 * JO),
    (CORP_CODE["000270"], "2026", "11013", "CFS"):
        bs_payload(equity_total=62 * JO, parent_equity=60 * JO, noncontrolling=2 * JO),
    (CORP_CODE["005380"], "2026", "11013", "CFS"):
        bs_payload(equity_total=90 * JO, parent_equity=80 * JO, noncontrolling=10 * JO),
    (CORP_CODE["005387"], "2026", "11013", "CFS"):
        bs_payload(equity_total=90 * JO, parent_equity=80 * JO),
    # 별도재무제표만 제출 (지배주주지분 개념 없음)
    (CORP_CODE["333333"], "2026", "11013", "OFS"):
        bs_payload(equity_total=10 * JO),
    (CORP_CODE["222222"], "2026", "11013", "CFS"):
        bs_payload(equity_total=-1 * JO, parent_equity=-1 * JO),
    # 111111 은 어떤 조합에도 없음 -> 전부 013
}


class FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload, self.content, self.status_code = payload, content, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """DartClient가 쓰는 requests.Session 대체."""

    def get(self, url, params=None, timeout=None):
        assert url.endswith("/fnlttSinglAcntAll.json"), url
        assert params["crtfc_key"] == FAKE_KEY, "API 키가 전달되지 않았습니다"
        key = (params["corp_code"], params["bsns_year"],
               params["reprt_code"], params["fs_div"])
        return FakeResponse(payload=DART_FIXTURES.get(key, NO_DATA))


def fake_corpcode_zip():
    root = ET.Element("result")
    for stock_code, corp_code in CORP_CODE.items():
        el = ET.SubElement(root, "list")
        ET.SubElement(el, "corp_code").text = corp_code
        ET.SubElement(el, "corp_name").text = fake_ticker_name(stock_code)
        ET.SubElement(el, "stock_code").text = stock_code
        ET.SubElement(el, "modify_date").text = "20260101"
    # 비상장 법인 (stock_code 공백) — 매핑에서 걸러져야 한다
    el = ET.SubElement(root, "list")
    ET.SubElement(el, "corp_code").text = "99999999"
    ET.SubElement(el, "corp_name").text = "비상장법인"
    ET.SubElement(el, "stock_code").text = " "
    ET.SubElement(el, "modify_date").text = "20260101"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", ET.tostring(root, encoding="utf-8"))
    return buf.getvalue()


# --- 대체 소스(FDR 캐시 CSV) 픽스처 --------------------------------------
# 실제 파일과 동일한 컬럼 구성. KOSPI(STK)만 걸러져야 한다.
FDR_HEADER = (",Code,ISU_CD,Name,Market,Dept,Close,ChangeCode,Changes,ChagesRatio,"
              "Open,High,Low,Volume,Amount,Marcap,Stocks,MarketId")


def fdr_csv_body():
    lines = [FDR_HEADER]
    for i, (code, name, price, cap, shares, _pbr) in enumerate(MARKET_ROWS):
        lines.append(f"{i},{code},KR7{code}00{i},{name},KOSPI,,{price},2,0,0.0,"
                     f"{price},{price},{price},1,1,{cap},{shares},STK")
    # 코스닥 종목 — 걸러져야 한다
    lines.append(f"{len(MARKET_ROWS)},999999,KR7999999009,코스닥종목,KOSDAQ,,1000,2,0,0.0,"
                 f"1000,1000,1000,1,1,{9 * JO},1000000,KSQ")
    return ("\n".join(lines) + "\n").encode("utf-8")


# 기준일에는 파일이 없고(휴장일) 하루 전에 있는 상황을 재현한다
FDR_AVAILABLE_DATE = "2026-07-27"


def fake_requests_get(url, params=None, timeout=None):
    if "corpCode.xml" in url:
        assert params["crtfc_key"] == FAKE_KEY
        return FakeResponse(content=fake_corpcode_zip())
    if "fdr_krx_data_cache" in url:
        if FDR_AVAILABLE_DATE in url:
            return FakeResponse(content=fdr_csv_body())
        return FakeResponse(content=b"404: Not Found", status_code=404)
    raise AssertionError(f"예상하지 못한 URL: {url}")


# =============================================================================
# 테스트 본체
# =============================================================================

def install_fakes(krx_down=False):
    """krx_down=True 이면 pykrx가 KRX 로그인 미설정 상태처럼 실패한다."""
    if krx_down:
        def dead(*a, **kw):
            # KRX가 빈 응답을 줄 때 pykrx가 내는 실제 예외
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        k.stock.get_market_cap_by_ticker = dead
        k.stock.get_market_fundamental_by_ticker = dead
        k.stock.get_market_ticker_name = dead
        k.stock.get_nearest_business_day_in_a_week = dead
    else:
        k.stock.get_market_cap_by_ticker = fake_market_cap
        k.stock.get_market_fundamental_by_ticker = fake_fundamental
        k.stock.get_market_ticker_name = fake_ticker_name
        k.stock.get_nearest_business_day_in_a_week = lambda date=None, prev=True: BASE_DATE
    k.requests.get = fake_requests_get
    k.requests.Session = FakeSession
    k.BASE_DATE = BASE_DATE
    k.MARKET_SOURCE = "auto"
    k.DART_SLEEP_SEC = 0
    os.environ["DART_API_KEY"] = FAKE_KEY


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) < tol


def test_full_run():
    install_fakes()
    workdir = tempfile.mkdtemp(prefix="screener_test_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        df = k.run()

        # --- 컬럼 구성 (요청 스펙 + 플래그) ---
        assert list(df.columns) == [
            "종목코드", "종목명", "종가", "시가총액(억)", "자기자본(억)", "BPS",
            "계산PBR", "시장PBR", "괴리율(%)", "기준보고서",
            "우선주존재", "자기자본기준", "재무제표구분",
        ], list(df.columns)

        # --- 계산PBR 오름차순, 0 < PBR <= 1.0 만 통과 ---
        assert list(df["종목코드"]) == ["333333", "005380", "000270"], list(df["종목코드"])
        assert list(df["계산PBR"]) == sorted(df["계산PBR"])
        assert df["계산PBR"].between(0, 1.0, inclusive="right").all()

        row = df.set_index("종목코드")

        # --- 계산식 ---
        # 기아: 시총 42조 / 지배주주지분 60조 = 0.7, BPS = 60조/4억주 = 150,000
        assert approx(row.loc["000270", "계산PBR"], 0.7)
        assert approx(row.loc["000270", "BPS"], 150_000)
        assert approx(row.loc["000270", "자기자본(억)"], 600_000)      # 60조 = 60만억
        assert approx(row.loc["000270", "시가총액(억)"], 420_000)
        # 괴리율 = (0.7 - 0.75) / 0.75 * 100 = -6.67
        assert approx(row.loc["000270", "괴리율(%)"], -6.67)
        # 자본총계(62조)가 아니라 지배주주지분(60조)을 썼는지
        assert row.loc["000270", "자기자본기준"] == "지배주주지분"
        assert row.loc["000270", "재무제표구분"] == "연결"

        # 현대차: 40조 / 80조 = 0.5
        assert approx(row.loc["005380", "계산PBR"], 0.5)
        assert approx(row.loc["005380", "BPS"], 400_000)

        # 별도만 공시: CFS 실패 후 OFS로 대체, 자본총계 사용
        assert approx(row.loc["333333", "계산PBR"], 0.4)
        assert row.loc["333333", "자기자본기준"] == "자본총계"
        assert row.loc["333333", "재무제표구분"] == "별도"

        # --- 기준보고서 라벨 ---
        assert set(df["기준보고서"]) == {"2026 1Q"}, set(df["기준보고서"])

        # --- 우선주 플래그 ---
        assert row.loc["005380", "우선주존재"] == "Y", "현대차2우B가 있으므로 Y"
        assert row.loc["000270", "우선주존재"] == "N"
        assert row.loc["333333", "우선주존재"] == "N"

        # --- 제외 사유별 집계 ---
        exc = pd.read_csv(k.EXCLUDED_CSV, dtype={"종목코드": str})
        by_code = exc.set_index("종목코드")["단계"].to_dict()
        assert by_code["444444"] == "시총필터"
        assert by_code["005387"] == "우선주제외"
        assert by_code["555555"] == "DART매핑"
        assert by_code["111111"] == "DART조회"
        assert by_code["222222"] == "자본잠식"
        assert by_code["005930"] == "PBR필터"
        assert len(exc) == 6, exc
        # 시도한 보고서가 사유에 남는지 (디버깅용)
        assert "2026 1Q" in exc.set_index("종목코드").loc["111111", "사유"]
        # 키가 CSV로 새지 않는지
        assert not exc["사유"].str.contains(FAKE_KEY).any()

        # --- CSV 인코딩: utf-8-sig (엑셀 한글 깨짐 방지) ---
        with open(k.OUTPUT_CSV, "rb") as fp:
            head = fp.read(3)
        assert head == b"\xef\xbb\xbf", "utf-8-sig BOM이 없습니다"
        reread = pd.read_csv(k.OUTPUT_CSV, encoding="utf-8-sig", dtype={"종목코드": str})
        assert list(reread["종목명"]) == ["별도만공시", "현대차", "기아"]

        print("test_full_run: OK")
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


def test_debug_ticker_mode():
    """--ticker 모드: 시총·우선주 필터를 건너뛰고 debug_ 접두사로 저장."""
    install_fakes()
    workdir = tempfile.mkdtemp(prefix="screener_dbg_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        df = k.run(tickers=["444444", "005930"])   # 소형주 + PBR>1 종목
        # 시총필터를 건너뛰므로 둘 다 DART까지 가지만, PBR 필터는 그대로 적용된다
        assert df.empty or set(df["종목코드"]) <= {"444444", "005930"}
        assert os.path.exists(f"debug_{k.OUTPUT_CSV}")
        assert not os.path.exists(k.OUTPUT_CSV), "디버그 실행이 전체 결과를 덮어썼습니다"
        exc = pd.read_csv(f"debug_{k.EXCLUDED_CSV}", dtype={"종목코드": str})
        assert "시총필터" not in set(exc["단계"]), "디버그 모드에서 시총필터가 적용됨"
        print("test_debug_ticker_mode: OK")
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


def test_limit_mode():
    """--limit N: 시총 상위 N개만 처리하고 debug_ 접두사로 저장."""
    install_fakes()
    workdir = tempfile.mkdtemp(prefix="screener_lim_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        k.run(limit=2)   # 시총 상위 2개 = 삼성전자(600조), 기아(42조)
        exc = pd.read_csv(f"debug_{k.EXCLUDED_CSV}", dtype={"종목코드": str})
        processed = set(pd.read_csv(f"debug_{k.OUTPUT_CSV}", dtype={"종목코드": str})["종목코드"])
        assert processed == {"000270"}, processed
        assert not os.path.exists(k.OUTPUT_CSV)
        assert "DART조회" not in set(exc["단계"]), "상위 2종목 밖이 조회됨"
        print("test_limit_mode: OK")
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


def test_fdr_fallback_when_krx_down():
    """
    KRX가 로그인 세션을 요구해 pykrx가 빈 응답을 받는 상황.
    대체 소스로 자동 전환되고, 시장PBR만 공란인 채 나머지는 정상 산출되어야 한다.
    """
    install_fakes(krx_down=True)
    workdir = tempfile.mkdtemp(prefix="screener_fdr_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        df = k.run()

        # 휴장일 되감기: 기준일(0728)에 파일이 없어 0727을 썼는지
        assert not df.empty, "대체 소스로 전환되지 않았습니다"
        # 스크리닝 결과 자체는 pykrx 경로와 동일해야 한다
        assert list(df["종목코드"]) == ["333333", "005380", "000270"], list(df["종목코드"])
        assert approx(df.set_index("종목코드").loc["000270", "계산PBR"], 0.7)
        # 시장PBR·괴리율만 공란
        assert df["시장PBR"].isna().all(), "PBR 없는 소스인데 값이 채워졌습니다"
        assert df["괴리율(%)"].isna().all()
        # 우선주 플래그는 대체 소스에서도 동작해야 한다 (종목명 기반)
        assert df.set_index("종목코드").loc["005380", "우선주존재"] == "Y"
        print("test_fdr_fallback_when_krx_down: OK")
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


def test_fdr_source_schema():
    """대체 소스 파서: 시장 필터, 컬럼 매핑, 휴장일 되감기."""
    install_fakes(krx_down=True)
    df, used = k._fetch_market_fdr(BASE_DATE)
    assert used == FDR_AVAILABLE_DATE.replace("-", ""), used     # 0728 -> 0727 되감기
    assert list(df.columns) == ["종목코드", "종목명", "종가", "시가총액", "상장주식수", "시장PBR"]
    assert "999999" not in set(df["종목코드"]), "코스닥 종목이 섞였습니다"
    assert len(df) == len(MARKET_ROWS)
    assert df["시장PBR"].isna().all()
    assert df.loc[df["종목코드"] == "005930", "시가총액"].iloc[0] == 600 * JO
    print("test_fdr_source_schema: OK")


def test_pykrx_source_forced_raises():
    """MARKET_SOURCE='pykrx' 로 고정하면 조용히 대체하지 않고 조치 방법을 알려야 한다."""
    install_fakes(krx_down=True)
    k.MARKET_SOURCE = "pykrx"
    try:
        k.fetch_market_snapshot(BASE_DATE)
    except SystemExit as exc:
        assert "KRX_ID" in str(exc) and "MARKET_SOURCE" in str(exc), exc
        print("test_pykrx_source_forced_raises: OK")
    else:
        raise AssertionError("pykrx 고정인데 실패를 알리지 않았습니다")
    finally:
        k.MARKET_SOURCE = "auto"


def test_fatal_status_aborts():
    """호출 한도 초과(020)는 남은 종목을 헛돌지 않고 즉시 중단해야 한다."""
    install_fakes()

    class QuotaSession:
        def get(self, url, params=None, timeout=None):
            return FakeResponse(payload={"status": "020", "message": "요청 제한 초과"})

    client = k.DartClient(FAKE_KEY)
    client.session = QuotaSession()
    resolver = k.PeriodResolver(k.build_period_candidates(BASE_DATE))
    try:
        k.fetch_equity(client, "00000100", resolver)
    except SystemExit as exc:
        assert "020" in str(exc), exc
        print("test_fatal_status_aborts: OK")
    else:
        raise AssertionError("020 응답에도 중단하지 않았습니다")


if __name__ == "__main__":
    test_full_run()
    test_debug_ticker_mode()
    test_limit_mode()
    test_fdr_source_schema()
    test_fdr_fallback_when_krx_down()
    test_pykrx_source_forced_raises()
    test_fatal_status_aborts()
    print("\nALL PIPELINE TESTS PASSED")
