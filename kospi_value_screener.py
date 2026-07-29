#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
코스피 밸류 스크리너 - 1단계(자체 계산 PBR) + 2단계(영업이익 연속 흑자)
=====================================================================

시장에서 제공하는 PBR을 그대로 믿지 않고, DART 재무상태표의 자기자본과
현재 시가총액을 직접 비교해 PBR을 다시 계산한다.

처리 순서
    1) pykrx로 코스피 전 종목의 종목코드/종목명/종가/시가총액/상장주식수/시장PBR 수집
    2) 시가총액 하한(기본 5,000억) 이상만 남겨 DART 호출 대상 축소
    3) 후보 종목만 OpenDART에서 가장 최근 보고서(분기 우선, 없으면 직전 연간)의
       재무상태표를 받아 자본총계 / 지배기업 소유주지분 추출
    4) 자기자본 = 지배주주지분(없으면 자본총계)
       BPS      = 자기자본 / 상장주식수
       계산PBR  = 시가총액 / 자기자본
       괴리율   = (계산PBR - 시장PBR) / 시장PBR * 100
    5) 0 < 계산PBR <= 1.0 인 종목만 최종 남기고 계산PBR 오름차순 정렬
    6) [2단계] 남은 종목의 영업이익 5년/3년 연속 흑자 여부를 Y/N 컬럼으로 표시.
       사업보고서 손익계산서가 한 응답에 당기·전기·전전기 3개년을 담으므로
       5년치를 모으는 데 종목당 사업보고서 2건이면 된다.
       기본은 '표시만' 하고 걸러내지 않는다 (STAGE2_REQUIRE 로 필터 전환 가능).

주의 (해석상의 한계)
    * 금융지주 / 은행 / 보험 / 증권은 자본 구조와 규제자본(BIS비율, RBC/K-ICS 등)의
      성격이 일반 제조업과 완전히 다르다. 자산 대부분이 금융자산이라 장부가치가
      시가에 가깝게 평가되고, 대손충당금·보험부채 할인율 가정에 따라 자기자본이
      크게 흔들린다. 따라서 "PBR 1배 미만 = 저평가"라는 해석을 그대로 적용하면 안 되며,
      본 스크리너의 결과에서 금융업종은 별도의 잣대로 다시 검토해야 한다.
    * 우선주가 상장된 종목은 pykrx 시가총액/상장주식수가 보통주 기준이라
      계산PBR이 실제보다 과소 계산된다. 해당 종목은 '우선주존재' 플래그로 표시한다.
    * 영업이익 연속 흑자는 '지속적으로 돈을 버는가'를 볼 뿐, 이익의 질(일회성 손익,
      매출 추세, 부채)까지 보지는 않는다. Y가 곧 좋은 기업이라는 뜻은 아니다.
    * 회계기준 변경이나 사업 재편으로 과거치가 재작성되면 연도별 값이 달라질 수 있다.
      본 스크리너는 더 최신 보고서에 실린 값을 우선한다.

시장 데이터 소스
    2026년부터 data.krx.co.kr이 로그인 세션을 요구해, KRX_ID/KRX_PW 없이 pykrx를 쓰면
    빈 응답("Expecting value: line 1 column 1")이 돌아온다. 그래서 KRX 로그인이 필요 없는
    대체 소스(FinanceDataReader의 일자별 상장종목 캐시)를 두고 자동 전환한다.
    대체 소스에는 시장PBR이 없어 괴리율만 공란이 되고, 계산PBR 스크리닝은 그대로 동작한다.
    자세한 내용은 MARKET_SOURCE 상수 주석 참고.

실행
    코랩:  이 파일 전체를 셀에 붙여넣고 실행하거나 `%run kospi_value_screener.py`
    로컬:  export DART_API_KEY=... && python kospi_value_screener.py
    진단:  python kospi_value_screener.py --selftest
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile
import threading
import datetime as dt
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# =============================================================================
# 기준값 상수 (여기만 고치면 됨)
# =============================================================================

MARKET = "KOSPI"                    # pykrx 시장 구분
BASE_DATE = ""                      # 기준일자 "YYYYMMDD". 빈 문자열이면 오늘 기준
MIN_MARKET_CAP_KRW = 500_000_000_000   # 시가총액 하한: 5,000억원
MAX_CALC_PBR = 1.0                  # 계산PBR 상한 (이하만 통과)
MIN_CALC_PBR = 0.0                  # 계산PBR 하한 (초과만 통과 = 자본잠식/음수 제외)

DART_WORKERS = 4                    # DART 동시 조회 스레드 수 (1이면 순차).
                                    # 응답이 종목당 수 초라 순차 조회는 300종목에 30분을 넘긴다.
                                    # 호출 '총량'은 그대로이고 대기 시간만 겹친다.
DART_SLEEP_SEC = 0.12               # DART 호출 간 대기 (분당 호출 제한 회피)
DART_TIMEOUT_SEC = 20               # DART 요청 타임아웃
DART_MAX_RETRY = 3                  # 네트워크 오류 시 재시도 횟수
MAX_PERIOD_TRIES = 4                # 한 종목당 시도할 보고서 후보 개수 상한
UNAVAILABLE_STRIKES = 12            # 특정 보고서가 N회 연속 실패(성공 0회)하면 미공시로 보고 건너뜀

# --- 2단계: 영업이익 연속 흑자 ------------------------------------------------
# 필터로 걸러내지 않고 Y/N 컬럼으로 표시만 한다 (몇 개가 살아남는지 먼저 보기 위함).
# 걸러내려면 STAGE2_REQUIRE 를 "5년" 또는 "3년" 으로 바꾼다.
STAGE2_ENABLED = True
PROFIT_YEARS_LONG = 5               # 장기 연속 흑자 판정 연수
PROFIT_YEARS_SHORT = 3              # 단기 연속 흑자 판정 연수
STAGE2_REQUIRE = ""                 # "" | "5년" | "3년"  — 통과 조건으로 쓸지 여부

OUTPUT_CSV = "kospi_value_screener_stage1.csv"
EXCLUDED_CSV = "kospi_value_screener_stage1_excluded.csv"   # 제외 사유 상세 (빈 문자열이면 저장 안 함)

# --- 디버깅 / 시험 실행 옵션 (CLI 인자로도 지정 가능) -------------------------
#   --selftest          : pykrx / DART 연결과 파싱을 3단계로 점검하고 종료
#   --limit 20          : 시총 상위 20종목만 처리 (전체 실행 전 빠른 확인)
#   --ticker 005930 ... : 특정 종목만 추적 (시총·우선주 필터 건너뜀, 상세 로그)
LIMIT_CANDIDATES = 0            # 0이면 전체 처리
DEBUG_TICKERS: list[str] = []   # 예: ["005930", "000660"]

EOK = 100_000_000                   # 1억 (원 -> 억 변환용)

DART_BASE = "https://opendart.fss.or.kr/api"

# 재무상태표 계정 매칭용
BS_SJ_DIV = "BS"
EQUITY_TOTAL_IDS = {"ifrs-full_Equity", "ifrs_Equity"}
PARENT_EQUITY_IDS = {
    "ifrs-full_EquityAttributableToOwnersOfParent",
    "ifrs_EquityAttributableToOwnersOfParent",
}
EQUITY_TOTAL_NAMES = ("자본총계",)
PARENT_EQUITY_NAMES = (
    "지배기업의소유주에게귀속되는자본",
    "지배기업소유주지분",
    "지배기업의소유주지분",
    "지배주주지분",
    "지배기업지분",
)

# 손익계산서 영업이익 계정 매칭용 (2단계)
IS_SJ_DIVS = ("IS", "CIS")          # 손익계산서 / 포괄손익계산서. IS를 우선한다
OPERATING_PROFIT_IDS = {
    "dart_OperatingIncomeLoss",
    "ifrs-full_ProfitLossFromOperatingActivities",
    "ifrs_ProfitLossFromOperatingActivities",
}
# norm_name()으로 괄호·공백을 지운 뒤 '정확히' 일치하는 것만 쓴다.
# 부분 일치를 허용하면 '영업이익률', '충당금적립전영업이익' 같은 계정이 섞인다.
OPERATING_PROFIT_NAMES = ("영업이익", "영업이익손실", "영업손익", "영업손실")
ANNUAL_REPRT_CODE = "11011"

# 보고서 코드 -> (라벨 접미사, 분기 끝 월/일, 공시 여유를 둔 조회 가능 시점 월/일, 연도 오프셋)
REPRT_CODES = {
    "11013": ("1Q", (3, 31), (5, 20), 0),    # 1분기보고서
    "11012": ("2Q", (6, 30), (8, 20), 0),    # 반기보고서
    "11014": ("3Q", (9, 30), (11, 20), 0),   # 3분기보고서
    "11011": ("FY", (12, 31), (3, 25), 1),   # 사업보고서 (다음 해 3월 말 공시)
}


# =============================================================================
# 의존성 (코랩에서 없으면 자동 설치)
# =============================================================================

def _ensure_deps() -> None:
    need = []
    for mod, pkg in (("pykrx", "pykrx"), ("requests", "requests"),
                     ("pandas", "pandas"), ("tqdm", "tqdm")):
        try:
            __import__(mod)
        except ImportError:
            need.append(pkg)
    if need:
        print(f"[setup] 설치 중: {' '.join(need)}")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *need])


_ensure_deps()

import pandas as pd            # noqa: E402
import requests                # noqa: E402
from pykrx import stock        # noqa: E402

try:
    from tqdm.auto import tqdm  # noqa: E402
except ImportError:             # tqdm이 끝내 없으면 진행률 없이 진행
    def tqdm(it, **kwargs):     # type: ignore
        return it


# =============================================================================
# 유틸
# =============================================================================

_SECRETS: list[str] = []


def remember_secret(value: str) -> None:
    """로그·예외 메시지에서 가려야 할 값(API 키)을 등록한다."""
    if value and value not in _SECRETS:
        _SECRETS.append(value)


def redact(text) -> str:
    """
    requests 예외 메시지에는 요청 URL이 통째로 들어있어 crtfc_key가 그대로 노출된다.
    로그를 그대로 복사해 공유해도 키가 새지 않도록 가린다.
    """
    out = str(text)
    for secret in _SECRETS:
        out = out.replace(secret, "***REDACTED***")
    return re.sub(r"(crtfc_key=)[^&\s'\"]+", r"\1***REDACTED***", out)


def log(msg: str) -> None:
    print(redact(msg), flush=True)


def to_num(raw) -> float | None:
    """DART 금액 문자열 -> float. 결측/'-'/빈칸은 None. (1,234) 형태는 음수."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--", "N/A", "nan", "None"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if s.startswith("△") or s.startswith("▲"):
        neg = True
        s = s[1:]
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def norm_name(s: str) -> str:
    """계정명 비교용 정규화: 공백/괄호/특수문자 제거."""
    return re.sub(r"[\s ().,·\-]", "", str(s or ""))


ENV_KEY_HINT = (
    "환경변수로 지정한 뒤 다시 실행하세요:\n"
    "    import os\n"
    "    os.environ['DART_API_KEY'] = '발급받은_키'\n"
    "  (또는 코랩 왼쪽 🔑 시크릿에 DART_API_KEY 등록 후 '노트북 액세스' 허용)"
)


def _accept_key(raw, source: str) -> str:
    """
    입력받은 값을 키로 채택한다.

    코랩은 표준 getpass를 자체 구현으로 바꿔치기해 두는데, 브라우저와의 통신이
    실패하면 문자열 대신 dict를 돌려준다. 그대로 .strip()을 부르면
    "AttributeError: 'dict' object has no attribute 'strip'" 로 죽으므로
    타입을 먼저 확인하고, 실패 시 조치 방법을 알려준다.
    """
    if not isinstance(raw, str):
        raise SystemExit(
            f"{source}에서 키를 문자열로 받지 못했습니다 (받은 타입: {type(raw).__name__}).\n"
            f"  코랩 입력창이 제대로 동작하지 않는 상태입니다. {ENV_KEY_HINT}")
    key = raw.strip()
    if not key:
        raise SystemExit(f"DART API 키가 비어 있습니다.\n  {ENV_KEY_HINT}")
    remember_secret(key)
    # 같은 세션에서 다시 실행할 때 입력창을 또 띄우지 않도록 저장해 둔다.
    os.environ["DART_API_KEY"] = key
    return key


def get_api_key() -> str:
    """DART API 키를 환경변수 또는 입력창에서 받는다. 하드코딩 금지."""
    env_key = os.environ.get("DART_API_KEY", "")
    if isinstance(env_key, str) and env_key.strip():
        log("[setup] DART_API_KEY 환경변수 사용")
        return _accept_key(env_key, "환경변수")

    # 코랩 시크릿(userdata) 지원
    try:
        from google.colab import userdata  # type: ignore
        secret = userdata.get("DART_API_KEY")
        if secret:
            log("[setup] 코랩 시크릿(DART_API_KEY) 사용")
            return _accept_key(secret, "코랩 시크릿")
    except Exception:
        pass

    from getpass import getpass
    try:
        raw = getpass("OpenDART API key를 입력하세요 (화면에 표시되지 않음): ")
    except Exception as exc:
        raise SystemExit(
            f"키 입력창을 띄우지 못했습니다 ({type(exc).__name__}). {ENV_KEY_HINT}") from None
    return _accept_key(raw, "입력창")


def resolve_base_date(raw: str) -> str:
    """기준일자를 직전 영업일로 보정한다."""
    date = raw.strip() or dt.date.today().strftime("%Y%m%d")
    try:
        return stock.get_nearest_business_day_in_a_week(date=date, prev=True)
    except TypeError:
        # 구버전 pykrx 시그니처 호환
        try:
            return stock.get_nearest_business_day_in_a_week(date)
        except Exception:
            pass
    except Exception:
        pass
    # KRX가 응답하지 않으면 여기서 더 두드려봐야 같은 오류만 반복된다.
    # 입력 날짜를 그대로 돌려주고, 대체 소스가 직전 영업일을 스스로 찾게 한다.
    log("      [warn] KRX 영업일 조회 실패 — 입력 날짜를 그대로 사용합니다.")
    return date


# =============================================================================
# 1) 시장 데이터 — pykrx(주) / FDR 캐시(대체)
# =============================================================================
#
# 2026년부터 data.krx.co.kr의 JSON 엔드포인트가 로그인 세션을 요구하면서,
# KRX_ID/KRX_PW 없이 pykrx를 쓰면 빈 응답이 돌아온다
# ("Expecting value: line 1 column 1 (char 0)" = JSON 파싱 실패).
# 그래서 KRX 로그인이 필요 없는 대체 소스를 둔다.
#
#   MARKET_SOURCE = "auto"   pykrx 먼저, 실패하면 FDR 캐시 (기본)
#                 = "pykrx"  pykrx만 (KRX_ID/KRX_PW 설정 시)
#                 = "fdr"    FDR 캐시만 (KRX 계정 없이 쓰는 경우)

MARKET_SOURCE = "auto"

# FinanceDataReader가 관리하는 일자별 KRX 상장 종목 스냅샷 (GitHub 정적 파일).
# KRX 로그인이 필요 없고 종가/시가총액/상장주식수를 모두 담고 있다. 단, PBR은 없다.
FDR_CACHE_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
                 "refs/heads/master/data/listing/krx/{date}.csv")
FDR_MARKET_ID = {"KOSPI": "STK", "KOSDAQ": "KSQ", "KONEX": "KNX"}
FDR_LOOKBACK_DAYS = 10          # 휴장일이면 하루씩 되감으며 찾는다


def _fetch_market_pykrx(base_date: str) -> pd.DataFrame:
    """pykrx 경로. 시장PBR까지 한 번에 얻을 수 있는 정식 경로."""
    cap = stock.get_market_cap_by_ticker(base_date, market=MARKET)
    if cap is None or cap.empty:
        raise RuntimeError(f"{base_date} 기준 {MARKET} 시가총액 데이터가 비어 있습니다.")

    try:
        fund = stock.get_market_fundamental_by_ticker(base_date, market=MARKET)
    except Exception as exc:
        log(f"      [warn] 시장PBR 조회 실패({type(exc).__name__}) — 괴리율은 공란이 됩니다.")
        fund = None
    if fund is None or fund.empty:
        fund = pd.DataFrame(index=cap.index)
    if "PBR" not in fund.columns:
        fund["PBR"] = float("nan")

    df = cap[["종가", "시가총액", "상장주식수"]].join(fund[["PBR"]], how="left")
    df = df.rename(columns={"PBR": "시장PBR"})
    df.index.name = "종목코드"
    df = df.reset_index()
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)

    names = {}
    for code in df["종목코드"]:
        try:
            names[code] = stock.get_market_ticker_name(code)
        except Exception:
            names[code] = ""
    df["종목명"] = df["종목코드"].map(names)
    return df


def _fetch_market_fdr(base_date: str) -> tuple[pd.DataFrame, str]:
    """
    FDR 캐시 경로. KRX 로그인 없이 동작하지만 시장PBR이 없다.
    요청일에 파일이 없으면(휴장일) 하루씩 되감으며 찾는다.
    반환: (데이터프레임, 실제로 사용한 날짜)
    """
    if MARKET not in FDR_MARKET_ID:
        raise RuntimeError(f"대체 소스가 지원하지 않는 시장입니다: {MARKET}")

    cur = dt.datetime.strptime(base_date, "%Y%m%d").date()
    raw = None
    used = ""
    for _ in range(FDR_LOOKBACK_DAYS):
        url = FDR_CACHE_URL.format(date=cur.isoformat())
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200 and resp.content:
                raw, used = resp.content, cur.strftime("%Y%m%d")
                break
        except Exception as exc:
            log(f"      [warn] 대체 소스 조회 실패 {cur}: {type(exc).__name__}")
        cur -= dt.timedelta(days=1)

    if raw is None:
        raise RuntimeError(
            f"대체 소스에서 {base_date} 이전 {FDR_LOOKBACK_DAYS}일치 데이터를 찾지 못했습니다.")

    df = pd.read_csv(io.BytesIO(raw), dtype={"Code": str, "MarketId": str})
    df = df[df["MarketId"] == FDR_MARKET_ID[MARKET]].copy()
    df = df.rename(columns={"Code": "종목코드", "Name": "종목명", "Close": "종가",
                            "Marcap": "시가총액", "Stocks": "상장주식수"})
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    df["시장PBR"] = float("nan")        # 이 소스에는 PBR이 없다
    return df[["종목코드", "종목명", "종가", "시가총액", "상장주식수", "시장PBR"]], used


def fetch_market_snapshot(base_date: str) -> tuple[pd.DataFrame, str, str]:
    """
    코스피 전 종목의 종가/시가총액/상장주식수/시장PBR/종목명.
    반환: (데이터프레임, 실제 사용한 기준일, 사용한 소스명)
    """
    log(f"[1/6] {MARKET} 시장 데이터 수집 (기준일 {base_date}, source={MARKET_SOURCE})")
    df, used_date, source = None, base_date, ""

    if MARKET_SOURCE in ("auto", "pykrx"):
        try:
            df, source = _fetch_market_pykrx(base_date), "pykrx"
        except Exception as exc:
            msg = f"      [warn] pykrx 실패: {type(exc).__name__}: {exc}"
            log(msg[:300])
            if MARKET_SOURCE == "pykrx":
                raise SystemExit(
                    "pykrx 경로가 실패했습니다. KRX가 로그인 세션을 요구하는 경우이므로\n"
                    "  (1) KRX_ID / KRX_PW 환경변수를 설정하거나\n"
                    "  (2) MARKET_SOURCE = \"fdr\" 로 바꿔 대체 소스를 쓰세요.") from None
            log("      → KRX 로그인이 필요한 상태로 보입니다. 대체 소스로 전환합니다.")

    if df is None:
        df, used_date = _fetch_market_fdr(base_date)
        source = "fdr-cache"
        log(f"      [info] 대체 소스 사용(기준일 {used_date}). "
            f"이 소스에는 시장PBR이 없어 '시장PBR'·'괴리율(%)'은 공란이 됩니다.")
        log("             계산PBR 스크리닝 자체에는 영향이 없습니다.")

    for col in ("종가", "시가총액", "상장주식수", "시장PBR"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # pykrx는 결측 PBR을 0으로 채워 내려주므로 결측으로 되돌린다.
    df.loc[df["시장PBR"] <= 0, "시장PBR"] = float("nan")

    log(f"      전 종목 {len(df)}개 수집 (source={source})")
    return df.reset_index(drop=True), used_date, source


# 우선주 종목명은 '...우', '...우B', '...2우B', '...3우C' 로 끝나고,
# 전환우선주는 뒤에 괄호가 더 붙는다: 'CJ4우(전환)', 'DL이앤씨2우(전환)'.
# '대우건설', '우리금융지주', '한국항공우주'처럼 '우'가 들어가도 끝이 아니면 보통주다.
PREF_NAME_RE = re.compile(r"\d?우[A-Za-z]?(\([^)]*\))?$")


def is_preferred_name(name: str) -> bool:
    return bool(PREF_NAME_RE.search((name or "").strip()))


def build_preferred_map(all_codes: list[str], name_by_code: dict[str, str]) -> dict[str, bool]:
    """
    보통주 종목별로 '같은 회사의 우선주가 함께 상장되어 있는가'를 판별한다.

    한국거래소 종목코드는 같은 회사의 우선주가 보통주와 앞 5자리를 공유하고
    끝자리만 다르다(5/7/9/K 등). 코드 앞 5자리 그룹 안에 종목명이 우선주 형태인
    종목이 있으면 그 그룹 전체를 '우선주 존재'로 표시한다.
    """
    pref_stems = {
        code[:5] for code in all_codes
        if is_preferred_name(name_by_code.get(code, ""))
    }
    return {code: (code[:5] in pref_stems and not is_preferred_name(name_by_code.get(code, "")))
            for code in all_codes}


# =============================================================================
# 2) DART: corp_code 매핑
# =============================================================================

def fetch_corp_code_map(api_key: str) -> dict[str, str]:
    """상장 종목코드(6자리) -> DART corp_code(8자리) 매핑."""
    log("[3/6] DART 기업 고유번호(corpCode) 내려받는 중")
    try:
        resp = requests.get(f"{DART_BASE}/corpCode.xml",
                            params={"crtfc_key": api_key}, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        # 예외 메시지에 요청 URL(=키 포함)이 들어있어 그대로 흘리지 않는다.
        raise SystemExit(redact(f"corpCode 호출 실패 — {type(exc).__name__}: {exc}")) from None
    if not resp.content.startswith(b"PK"):
        head = redact(resp.content[:400].decode("utf-8", "replace"))
        raise SystemExit(f"corpCode 응답이 ZIP이 아닙니다. API 키를 확인하세요.\n{head}")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])

    mapping: dict[str, str] = {}
    for node in ET.fromstring(xml_bytes).iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if stock_code and stock_code != " " and corp_code:
            mapping[stock_code.zfill(6)] = corp_code
    log(f"      상장사 {len(mapping)}건 매핑")
    return mapping


# =============================================================================
# 3) DART: 보고서 후보 & 재무상태표
# =============================================================================

@dataclass(frozen=True)
class Period:
    year: int          # 사업연도 (bsns_year)
    reprt_code: str
    label: str         # 예: "2026 1Q", "2025 FY"
    end: dt.date       # 회계기간 종료일 (최신성 정렬용)


def build_period_candidates(base_date: str, lookback_years: int = 3) -> list[Period]:
    """
    기준일 시점에 조회 가능한 보고서를 최신순으로 나열한다.
    분기/반기 보고서가 앞에 오고, 연간(사업보고서)은 기간 종료일 순서에 따라 뒤로 밀린다.
    """
    base = dt.datetime.strptime(base_date, "%Y%m%d").date()
    out: list[Period] = []
    for year in range(base.year, base.year - lookback_years - 1, -1):
        for code, (suffix, (em, ed), (am, ad), yoff) in REPRT_CODES.items():
            end = dt.date(year, em, ed)
            avail = dt.date(year + yoff, am, ad)
            if avail > base:
                continue
            out.append(Period(year=year, reprt_code=code,
                              label=f"{year} {suffix}", end=end))
    out.sort(key=lambda p: p.end, reverse=True)
    return out


class PeriodResolver:
    """
    아직 공시되지 않은 보고서를 매 종목마다 두드리는 낭비를 막는다.
    성공 0회 + 연속 실패 UNAVAILABLE_STRIKES회면 해당 보고서는 미공시로 보고 건너뛴다.
    """

    def __init__(self, candidates: list[Period]):
        self.candidates = candidates
        self.success: dict[Period, int] = {p: 0 for p in candidates}
        self.fail: dict[Period, int] = {p: 0 for p in candidates}
        self._announced: set[Period] = set()
        self._lock = threading.Lock()   # 여러 스레드가 같은 카운터를 갱신한다

    def _dead(self, p: Period) -> bool:
        return self.success[p] == 0 and self.fail[p] >= UNAVAILABLE_STRIKES

    def order(self) -> list[Period]:
        with self._lock:
            alive = [p for p in self.candidates if not self._dead(p)]
            return (alive or self.candidates)[:MAX_PERIOD_TRIES]

    def mark(self, p: Period, ok: bool) -> None:
        with self._lock:
            (self.success if ok else self.fail)[p] += 1
            announce = not ok and self._dead(p) and p not in self._announced
            if announce:
                self._announced.add(p)
        if announce:
            log(f"      [info] {p.label} 보고서는 아직 미공시로 판단, 이후 조회에서 제외")


class DartClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.calls = 0
        self.last_error = ""      # 마지막 실패 원인 (디버깅용 — 삼키지 않는다)
        self._local = threading.local()   # 세션은 스레드마다 따로 쓴다
        self._session_override = None
        self._lock = threading.Lock()

    @property
    def session(self):
        if self._session_override is not None:
            return self._session_override
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            self._local.session = sess
        return sess

    @session.setter
    def session(self, value) -> None:
        self._session_override = value

    def get_json(self, endpoint: str, params: dict) -> dict | None:
        params = {"crtfc_key": self.api_key, **params}
        for attempt in range(DART_MAX_RETRY):
            try:
                with self._lock:
                    self.calls += 1
                r = self.session.get(f"{DART_BASE}/{endpoint}",
                                     params=params, timeout=DART_TIMEOUT_SEC)
                time.sleep(DART_SLEEP_SEC)          # 호출 제한 회피
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                self.last_error = redact(f"{type(exc).__name__}: {exc}")
                if attempt == DART_MAX_RETRY - 1:
                    return None
                time.sleep(1.5 * (attempt + 1))     # 백오프
        return None


# 종목별로 넘길 수 없는 에러(키 문제/호출한도 초과) — 만나면 즉시 중단한다.
DART_FATAL_STATUS = {
    "010": "등록되지 않은 API 키",
    "011": "사용할 수 없는 API 키(일시 사용 중지 등)",
    "012": "접근할 수 없는 IP",
    "020": "요청 제한을 초과했습니다 (일일 20,000건)",
    "021": "조회 가능한 회사 개수 초과",
}


@dataclass
class BsResult:
    equity_total: float | None = None      # 자본총계
    parent_equity: float | None = None     # 지배기업 소유주지분
    fs_div: str = ""                       # CFS(연결) / OFS(별도)
    reason: str = ""                       # 실패 사유


def parse_balance_sheet(payload: dict) -> BsResult:
    """fnlttSinglAcntAll 응답에서 자본총계 / 지배주주지분을 추출."""
    res = BsResult()
    status = payload.get("status")
    if status != "000":
        res.reason = f"DART status={status} ({payload.get('message', '')})"
        return res

    for item in payload.get("list", []):
        if item.get("sj_div") != BS_SJ_DIV:
            continue
        acc_id = (item.get("account_id") or "").strip()
        nm = norm_name(item.get("account_nm"))
        amount = to_num(item.get("thstrm_amount"))
        if amount is None:
            continue

        if acc_id in PARENT_EQUITY_IDS or (
            "비지배" not in nm and any(k in nm for k in PARENT_EQUITY_NAMES)
        ):
            if res.parent_equity is None:
                res.parent_equity = amount
            continue

        if acc_id in EQUITY_TOTAL_IDS or (
            nm in EQUITY_TOTAL_NAMES or (nm.startswith("자본총계") and "비지배" not in nm)
        ):
            if res.equity_total is None:
                res.equity_total = amount

    if res.equity_total is None and res.parent_equity is None:
        res.reason = "재무상태표에서 자본총계/지배주주지분을 찾지 못함"
    return res


def fetch_equity(client: DartClient, corp_code: str,
                 resolver: PeriodResolver) -> tuple[BsResult | None, Period | None, str]:
    """
    최신 보고서부터 차례로 시도해 재무상태표를 가져온다.
    연결(CFS) 우선, 없으면 별도(OFS).
    반환: (결과, 사용한 보고서, 실패 사유)
    """
    tried = resolver.order()
    last_reason = "조회 가능한 보고서 없음"
    for period in tried:
        got_period = False
        for fs_div in ("CFS", "OFS"):
            payload = client.get_json("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(period.year),
                "reprt_code": period.reprt_code,
                "fs_div": fs_div,
            })
            if payload is None:
                last_reason = f"DART 호출 실패 [{period.label}/{fs_div}] {client.last_error}"
                continue
            status = payload.get("status")
            if status in DART_FATAL_STATUS:
                raise SystemExit(
                    f"DART 오류 {status}: {DART_FATAL_STATUS[status]} — 실행을 중단합니다."
                )
            res = parse_balance_sheet(payload)
            if res.equity_total is not None or res.parent_equity is not None:
                res.fs_div = fs_div
                resolver.mark(period, True)
                return res, period, ""
            last_reason = res.reason or last_reason
            if payload.get("status") == "000":
                got_period = True   # 보고서는 있는데 계정 파싱 실패
        resolver.mark(period, got_period)
    scope = ", ".join(p.label for p in tried) or "없음"
    return None, None, f"{last_reason} (시도한 보고서: {scope})"


# =============================================================================
# 2단계) 영업이익 연속 흑자
# =============================================================================
#
# 사업보고서 손익계산서는 한 응답에 당기·전기·전전기 3개년을 함께 담고 있다.
# 따라서 5년치를 모으는 데 종목당 사업보고서 2건(예: 2025 FY + 2022 FY)이면 충분하다.


def annual_report_years(latest_fy: int, n_years: int) -> list[int]:
    """n_years치를 덮는 데 필요한 최소한의 사업보고서 연도 목록(최신순)."""
    needed = set(range(latest_fy - n_years + 1, latest_fy + 1))
    years, y = [], latest_fy
    while needed and y > latest_fy - n_years - 3:
        years.append(y)
        needed -= {y, y - 1, y - 2}
        y -= 3
    return years


def latest_annual_year(periods: list[Period]) -> int | None:
    """조회 가능한 보고서 후보 중 가장 최근 사업보고서의 사업연도."""
    for p in periods:                      # periods는 최신순으로 정렬돼 있다
        if p.reprt_code == ANNUAL_REPRT_CODE:
            return p.year
    return None


def parse_operating_profit(payload: dict, year: int) -> dict[int, float]:
    """
    사업보고서 응답에서 영업이익 3개년을 뽑는다.
    반환: {연도: 영업이익(원)}  — 값이 없는 연도는 키 자체가 없다.
    """
    if payload.get("status") != "000":
        return {}

    chosen = None
    for item in payload.get("list", []):
        sj = item.get("sj_div")
        if sj not in IS_SJ_DIVS:
            continue
        acc_id = (item.get("account_id") or "").strip()
        nm = norm_name(item.get("account_nm"))
        if acc_id not in OPERATING_PROFIT_IDS and nm not in OPERATING_PROFIT_NAMES:
            continue
        # 손익계산서(IS)를 포괄손익계산서(CIS)보다 우선한다
        if chosen is None or (chosen[0] != "IS" and sj == "IS"):
            chosen = (sj, item)

    if chosen is None:
        return {}

    item = chosen[1]
    out = {}
    for offset, key in ((0, "thstrm_amount"), (1, "frmtrm_amount"), (2, "bfefrmtrm_amount")):
        val = to_num(item.get(key))
        if val is not None:
            out[year - offset] = val
    return out


def fetch_operating_profits(client: DartClient, corp_code: str, latest_fy: int,
                            n_years: int, prefer_cfs: bool) -> tuple[dict[int, float], str, str]:
    """
    최근 n_years치 영업이익을 모은다.
    반환: ({연도: 영업이익}, 사용한 재무제표구분, 실패 사유)
    """
    order = ["CFS", "OFS"] if prefer_cfs else ["OFS", "CFS"]
    profits: dict[int, float] = {}
    used_div = ""
    reasons: list[str] = []

    for year in annual_report_years(latest_fy, n_years):
        got = False
        detail: list[str] = []
        for fs_div in order:
            payload = client.get_json("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": ANNUAL_REPRT_CODE,
                "fs_div": fs_div,
            })
            if payload is None:
                detail.append(f"{fs_div} 호출실패({client.last_error})")
                continue
            status = payload.get("status")
            if status in DART_FATAL_STATUS:
                raise SystemExit(
                    f"DART 오류 {status}: {DART_FATAL_STATUS[status]} — 실행을 중단합니다.")
            found = parse_operating_profit(payload, year)
            if found:
                # 최신 보고서 값을 우선한다 (재작성된 과거치는 최신 보고서 기준이 정확)
                for y, v in found.items():
                    profits.setdefault(y, v)
                used_div = used_div or fs_div
                got = True
                break
            # 보고서 자체가 없는 것과, 보고서는 있는데 영업이익 계정이 없는 것은
            # 원인이 전혀 다르므로 구분해서 남긴다.
            detail.append(f"{fs_div} 보고서없음(status={status})" if status != "000"
                          else f"{fs_div} 영업이익 계정 없음")
        if not got:
            reasons.append(f"{year} FY: {' / '.join(detail)}")
    return profits, used_div, " ; ".join(reasons)


def profit_streak(profits: dict[int, float], latest_fy: int, n_years: int) -> str:
    """
    최근 n_years 연속 흑자 여부.
    Y = 전부 흑자, N = 한 해라도 적자, '-' = 해당 연도 데이터가 없어 판정 불가.
    """
    years = [latest_fy - i for i in range(n_years)]
    if any(y not in profits for y in years):
        return "-"
    return "Y" if all(profits[y] > 0 for y in years) else "N"


def format_profit_trend(profits: dict[int, float], latest_fy: int, n_years: int) -> str:
    """'2021:1,234 | 2022:-567 | ...' 형태의 억 단위 추이 문자열 (오래된 연도부터)."""
    parts = []
    for y in range(latest_fy - n_years + 1, latest_fy + 1):
        val = profits.get(y)
        parts.append(f"{y}:{val / EOK:,.0f}" if val is not None else f"{y}:-")
    return " | ".join(parts)


# =============================================================================
# 4~5) 계산 / 필터 / 출력
# =============================================================================

STAGE2_COLUMNS = ["영업이익5년연속흑자", "영업이익3년연속흑자",
                  "영업이익추이(억)", "영업이익기준"]


def attach_profit_streaks(df: pd.DataFrame, client: "DartClient", corp_map: dict,
                          periods: list[Period], excluded: "Excluded",
                          debug: bool = False) -> pd.DataFrame:
    """
    2단계: PBR 필터를 통과한 종목만 대상으로 영업이익 연속 흑자 여부를 채운다.
    기본은 표시만 하고 걸러내지 않는다 (STAGE2_REQUIRE 로 필터로 바꿀 수 있다).
    """
    if not STAGE2_ENABLED:
        log("[6/6] 2단계 비활성화(STAGE2_ENABLED=False) — 건너뜁니다.")
        return df
    if df.empty:
        log("[6/6] 1단계 통과 종목이 없어 2단계를 건너뜁니다.")
        return df

    latest_fy = latest_annual_year(periods)
    if latest_fy is None:
        log("[6/6] [warn] 조회 가능한 사업보고서가 없어 2단계를 건너뜁니다.")
        for col in STAGE2_COLUMNS:
            df[col] = "-"
        return df

    years = annual_report_years(latest_fy, PROFIT_YEARS_LONG)
    log(f"[6/6] 영업이익 연속 흑자 조회 ({len(df)}종목, "
        f"{latest_fy - PROFIT_YEARS_LONG + 1}~{latest_fy} / 사업보고서 {len(years)}건: "
        f"{', '.join(f'{y} FY' for y in years)})")

    window = list(range(latest_fy - PROFIT_YEARS_LONG + 1, latest_fy + 1))

    def probe(row) -> dict:
        code, name = row["종목코드"], row["종목명"]
        blank = {c: "-" for c in STAGE2_COLUMNS}
        corp_code = corp_map.get(code)
        if not corp_code:
            excluded.add(code, name, "영업이익조회",
                         "DART corp_code 없음(결과에는 남김)", verbose=False)
            return blank
        # 1단계에서 연결을 썼으면 연결을, 별도를 썼으면 별도를 우선한다 (기준 일관성)
        prefer_cfs = row.get("재무제표구분") != "별도"
        profits, used_div, reason = fetch_operating_profits(
            client, corp_code, latest_fy, PROFIT_YEARS_LONG, prefer_cfs)
        if debug:
            log(f"      [debug] {code} {name} 영업이익={profits} "
                f"기준={used_div or '-'} 사유={reason or '-'}")
        if not profits:
            excluded.add(code, name, "영업이익조회",
                         reason or "영업이익 데이터 없음(결과에는 남김)", verbose=False)
            return blank

        # 일부 연도만 빠진 경우도 사유를 남긴다. 조용히 넘기면 '-'가 왜 생겼는지
        # 알 수 없어 판정불가 종목을 추적할 방법이 사라진다.
        missing = [y for y in window if y not in profits]
        if missing:
            # 보고서를 못 받은 것과, 보고서는 받았는데 그 해 금액칸이 비어 있는 것은
            # 원인이 다르다. 후자는 재작성·사업재편 때 흔하다.
            why = reason or "보고서에 해당 연도 금액이 비어 있음"
            excluded.add(code, name, "영업이익부분결측",
                         f"{', '.join(str(y) for y in missing)}년 누락 — {why} (결과에는 남김)",
                         verbose=False)
        return {
            "영업이익5년연속흑자": profit_streak(profits, latest_fy, PROFIT_YEARS_LONG),
            "영업이익3년연속흑자": profit_streak(profits, latest_fy, PROFIT_YEARS_SHORT),
            "영업이익추이(억)": format_profit_trend(profits, latest_fy, PROFIT_YEARS_LONG),
            "영업이익기준": "연결" if used_div == "CFS" else "별도",
        }

    rows = [row for _, row in df.iterrows()]
    workers = max(1, int(DART_WORKERS))
    bar = tqdm(total=len(rows), desc="영업이익", unit="종목")
    results: dict[int, dict] = {}
    try:
        if workers == 1:
            for i, row in enumerate(rows):
                results[i] = probe(row)
                bar.update(1)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(probe, row): i for i, row in enumerate(rows)}
                try:
                    for fut in as_completed(futures):
                        results[futures[fut]] = fut.result()
                        bar.update(1)
                except (SystemExit, KeyboardInterrupt):
                    for f in futures:
                        f.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
    finally:
        bar.close()

    for col in STAGE2_COLUMNS:
        df[col] = [results.get(i, {}).get(col, "-") for i in range(len(rows))]

    for label, col in ((f"{PROFIT_YEARS_LONG}년", "영업이익5년연속흑자"),
                       (f"{PROFIT_YEARS_SHORT}년", "영업이익3년연속흑자")):
        counts = df[col].value_counts()
        log(f"      {label} 연속 흑자: Y {counts.get('Y', 0)} / "
            f"N {counts.get('N', 0)} / 판정불가 {counts.get('-', 0)}")

    # 판정불가가 왜 생겼는지 바로 알 수 있게 사유별로 집계해 보여준다.
    gaps = [r for r in excluded.rows if r["단계"] in ("영업이익조회", "영업이익부분결측")]
    if gaps:
        log(f"      [판정불가 원인] {len(gaps)}종목 — 상세는 제외 CSV의 "
            f"'영업이익조회'/'영업이익부분결측' 행 참고")
        buckets: dict[str, int] = {}
        for r in gaps:
            for token in ("보고서없음", "영업이익 계정 없음", "호출실패", "corp_code 없음"):
                if token in r["사유"]:
                    buckets[token] = buckets.get(token, 0) + 1
                    break
            else:
                buckets["기타"] = buckets.get("기타", 0) + 1
        for token, cnt in sorted(buckets.items(), key=lambda kv: -kv[1]):
            log(f"        - {token}: {cnt}")
        log(f"        예시: {gaps[0]['종목코드']} {gaps[0]['종목명']} — {gaps[0]['사유'][:110]}")

    if STAGE2_REQUIRE:
        col = {"5년": "영업이익5년연속흑자", "3년": "영업이익3년연속흑자"}.get(STAGE2_REQUIRE)
        if col is None:
            log(f"      [warn] STAGE2_REQUIRE 값이 잘못됐습니다: {STAGE2_REQUIRE!r} — 필터 미적용")
        else:
            drop = df[df[col] != "Y"]
            for _, r in drop.iterrows():
                excluded.add(r["종목코드"], r["종목명"], "영업이익필터",
                             f"{STAGE2_REQUIRE} 연속 흑자 {r[col]}", verbose=False)
            df = df[df[col] == "Y"].reset_index(drop=True)
            log(f"      [filter] {STAGE2_REQUIRE} 연속 흑자 조건 적용 -> {len(df)}종목")
    return df


@dataclass
class Excluded:
    rows: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, code: str, name: str, stage: str, reason: str, verbose: bool = True) -> None:
        reason = redact(reason)   # 사유에 DART 요청 URL이 섞일 수 있어 CSV에도 키를 남기지 않는다
        with self.lock:
            self.rows.append({"종목코드": code, "종목명": name, "단계": stage, "사유": reason})
        if verbose:
            log(f"      [skip] {code} {name}: {reason}")

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=["종목코드", "종목명", "단계", "사유"])


def run(limit: int = LIMIT_CANDIDATES, tickers: list[str] | None = None) -> pd.DataFrame:
    only = [str(t).zfill(6) for t in (DEBUG_TICKERS if tickers is None else tickers)]
    debug = bool(only)
    api_key = get_api_key()
    base_date = resolve_base_date(BASE_DATE)
    excluded = Excluded()

    # --- 1) 시장 데이터 ---------------------------------------------------
    market, base_date, market_source = fetch_market_snapshot(base_date)
    has_market_pbr = bool(market["시장PBR"].notna().any())
    name_by_code = dict(zip(market["종목코드"], market["종목명"]))
    pref_map = build_preferred_map(list(market["종목코드"]), name_by_code)

    # --- 2) 시가총액 필터 -------------------------------------------------
    total_listed = len(market)
    if debug:
        # 특정 종목만 추적: 시총/우선주 필터를 건너뛰고 그대로 통과시킨다.
        cand = market[market["종목코드"].isin(only)].copy()
        missing = [t for t in only if t not in set(market["종목코드"])]
        log(f"[2/6] 디버그 모드: {len(cand)}종목만 조회 (시총·우선주 필터 건너뜀)")
        if missing:
            log(f"      [warn] {MARKET}에서 찾지 못한 종목코드: {', '.join(missing)}")
    else:
        cand = market[market["시가총액"] >= MIN_MARKET_CAP_KRW].copy()
        dropped_cap = total_listed - len(cand)
        log(f"[2/6] 시가총액 {MIN_MARKET_CAP_KRW / EOK:,.0f}억 이상 필터: "
            f"{total_listed} -> {len(cand)}종목 (제외 {dropped_cap})")
        for _, row in market[market["시가총액"] < MIN_MARKET_CAP_KRW].iterrows():
            excluded.add(row["종목코드"], row["종목명"], "시총필터",
                         f"시가총액 {row['시가총액'] / EOK:,.0f}억 < 기준", verbose=False)

        # 우선주 종목 자체는 분석 대상이 아니다 (보통주만 남긴다)
        is_pref = cand["종목명"].map(is_preferred_name)
        for _, row in cand[is_pref].iterrows():
            excluded.add(row["종목코드"], row["종목명"], "우선주제외",
                         "우선주 종목(보통주만 분석)", verbose=False)
        cand = cand[~is_pref].copy()

        if limit and limit > 0 and len(cand) > limit:
            log(f"      [시험 실행] 시총 상위 {limit}종목만 처리합니다.")
            cand = cand.nlargest(limit, "시가총액").copy()

    # --- 3) DART ----------------------------------------------------------
    corp_map = fetch_corp_code_map(api_key)
    periods = build_period_candidates(base_date)
    if not periods:
        raise SystemExit("기준일 기준으로 조회 가능한 보고서 후보가 없습니다.")
    log(f"      보고서 후보(최신순): {', '.join(p.label for p in periods[:6])} ...")

    client = DartClient(api_key)
    resolver = PeriodResolver(periods)
    records: list[dict] = []

    def process(row) -> dict | None:
        """한 종목 처리. 제외되면 None을 반환하고 사유를 기록한다."""
        code, name = row["종목코드"], row["종목명"]
        corp_code = corp_map.get(code)
        if not corp_code:
            excluded.add(code, name, "DART매핑", "DART corp_code를 찾지 못함")
            return None

        res, period, reason = fetch_equity(client, corp_code, resolver)
        if res is None or period is None:
            excluded.add(code, name, "DART조회", reason)
            return None
        if debug:
            log(f"      [debug] {code} {name} corp_code={corp_code} "
                f"보고서={period.label} {'연결' if res.fs_div == 'CFS' else '별도'} | "
                f"자본총계={res.equity_total} 지배주주지분={res.parent_equity}")

        # 4) 자기자본 = 지배주주지분 우선, 없으면 자본총계
        equity = res.parent_equity if res.parent_equity is not None else res.equity_total
        equity_basis = "지배주주지분" if res.parent_equity is not None else "자본총계"
        if equity is None:
            excluded.add(code, name, "재무결측", "자기자본 값 없음")
            return None
        if equity <= 0:
            excluded.add(code, name, "자본잠식", f"자기자본 {equity / EOK:,.0f}억 <= 0")
            return None

        shares = row["상장주식수"]
        mcap = row["시가총액"]
        if not shares or shares <= 0:
            excluded.add(code, name, "결측", "상장주식수 없음")
            return None
        if not mcap or mcap <= 0:
            excluded.add(code, name, "결측", "시가총액 없음")
            return None

        bps = equity / shares
        calc_pbr = mcap / equity
        mkt_pbr = row["시장PBR"]
        gap = ((calc_pbr - mkt_pbr) / mkt_pbr * 100) if pd.notna(mkt_pbr) and mkt_pbr > 0 else float("nan")
        # 소스 전체에 PBR이 없는 경우(FDR 캐시)는 이미 한 번 안내했으므로 종목별로 반복하지 않는다.
        if pd.isna(gap) and has_market_pbr:
            log(f"      [warn] {code} {name}: 시장PBR 결측 -> 괴리율 계산 불가(결과에는 포함)")

        return {
            "종목코드": code,
            "종목명": name,
            "종가": int(row["종가"]),
            "시가총액(억)": round(mcap / EOK, 1),
            "자기자본(억)": round(equity / EOK, 1),
            "BPS": round(bps, 0),
            "계산PBR": round(calc_pbr, 4),
            "시장PBR": round(float(mkt_pbr), 4) if pd.notna(mkt_pbr) else float("nan"),
            "괴리율(%)": round(gap, 2) if pd.notna(gap) else float("nan"),
            "기준보고서": period.label,
            "우선주존재": "Y" if pref_map.get(code) else "N",
            "자기자본기준": equity_basis,
            "재무제표구분": "연결" if res.fs_div == "CFS" else "별도",
        }

    rows = [row for _, row in cand.iterrows()]
    workers = max(1, int(DART_WORKERS))
    log(f"[4/6] DART 재무상태표 조회 ({len(rows)}종목, 동시 {workers})")
    bar = tqdm(total=len(rows), desc="DART", unit="종목")
    if workers == 1:
        for row in rows:
            rec = process(row)
            if rec:
                records.append(rec)
            bar.update(1)
    else:
        # DART 응답이 종목당 수 초 걸려 순차 조회는 수백 종목에서 30분을 넘긴다.
        # 스레드는 응답 대기 시간만 겹치므로 호출 총량은 그대로다.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process, row) for row in rows]
            try:
                for fut in as_completed(futures):
                    rec = fut.result()
                    if rec:
                        records.append(rec)
                    bar.update(1)
            except (SystemExit, KeyboardInterrupt):
                # 키 오류·한도 초과 등은 남은 조회를 계속할 이유가 없다.
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                bar.close()
                raise
    bar.close()

    # --- 5) PBR 필터 & 정렬 ----------------------------------------------
    df = pd.DataFrame(records)
    log(f"[5/6] 계산 완료 {len(df)}종목 -> PBR 필터 "
        f"({MIN_CALC_PBR} < 계산PBR <= {MAX_CALC_PBR})")
    if debug and not df.empty:
        log("      [debug] PBR 필터 적용 전 계산 결과:")
        print(df.to_string(index=False))
    if not df.empty:
        fail = df[~((df["계산PBR"] > MIN_CALC_PBR) & (df["계산PBR"] <= MAX_CALC_PBR))]
        for _, r in fail.iterrows():
            excluded.add(r["종목코드"], r["종목명"], "PBR필터",
                         f"계산PBR {r['계산PBR']}", verbose=False)
        df = df[(df["계산PBR"] > MIN_CALC_PBR) & (df["계산PBR"] <= MAX_CALC_PBR)]
        df = df.sort_values("계산PBR", ascending=True).reset_index(drop=True)

    # --- 6) 2단계: 영업이익 연속 흑자 -------------------------------------
    df = attach_profit_streaks(df, client, corp_map, periods, excluded, debug)

    # --- 저장 & 요약 -------------------------------------------------------
    # 시험/디버그 실행 결과가 전체 실행 결과를 덮어쓰지 않도록 파일명을 분리한다.
    partial = debug or bool(limit and limit > 0)
    out_csv = f"debug_{OUTPUT_CSV}" if partial else OUTPUT_CSV
    exc_csv = (f"debug_{EXCLUDED_CSV}" if partial else EXCLUDED_CSV) if EXCLUDED_CSV else ""

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    exc_df = excluded.summary()
    if exc_csv:
        exc_df.to_csv(exc_csv, index=False, encoding="utf-8-sig")

    log("")
    log("=" * 72)
    log(f"기준일자          : {base_date} (시장데이터 source={market_source})")
    log(f"전체 상장종목     : {total_listed}")
    if debug:
        log(f"디버그 대상       : {len(cand)}종목 ({', '.join(only)})")
    else:
        log(f"시총 필터 통과    : {len(cand)} (하한 {MIN_MARKET_CAP_KRW / EOK:,.0f}억)")
    log(f"DART 호출 수      : {client.calls}")
    log(f"최종 통과 종목    : {len(df)}")
    log(f"제외 종목 수      : {len(exc_df)}")
    if not exc_df.empty:
        log("제외 사유별 집계  :")
        for stage, cnt in exc_df["단계"].value_counts().items():
            log(f"  - {stage}: {cnt}")
    log(f"결과 CSV          : {out_csv}")
    if exc_csv:
        log(f"제외 상세 CSV     : {exc_csv}")
    if not df.empty and (df["우선주존재"] == "Y").any():
        n = int((df["우선주존재"] == "Y").sum())
        log(f"[note] 우선주 상장 종목 {n}개 포함 — 보통주 시총만 반영되어 "
            f"계산PBR이 과소 계산됨 (우선주존재=Y)")
    if not has_market_pbr:
        log("[note] 시장PBR을 제공하지 않는 소스라 '시장PBR'·'괴리율(%)'은 공란입니다 "
            "(계산PBR 스크리닝은 정상)")
    log("[note] 금융지주/은행/보험/증권은 자본 구조가 달라 PBR 1배 미만을 "
        "그대로 저평가로 해석하면 안 됨")
    log("=" * 72)

    if not df.empty:
        with pd.option_context("display.max_rows", 50, "display.width", 200):
            print(df.head(30).to_string(index=False))

    # 코랩이면 결과 파일 자동 다운로드 (시험 실행은 제외)
    if not partial:
        try:
            from google.colab import files  # type: ignore
            files.download(out_csv)
        except Exception:
            pass

    return df


# =============================================================================
# 진단(self-test): 어디서 깨졌는지 3단계로 좁힌다
# =============================================================================

SELFTEST_TICKER = "005930"   # 삼성전자 — 연결/지배주주지분이 모두 있는 표준 케이스


def selftest() -> bool:
    """
    전체 실행 전에 pykrx / DART / 파싱을 순서대로 점검한다.
    실패한 첫 단계가 원인이므로, 위에서부터 하나씩 고치면 된다.
    """
    ok = True
    log("=" * 72)
    log("[진단 1/3] 시장 데이터 (DART 키 불필요)")
    base_date = None
    try:
        base_date = resolve_base_date(BASE_DATE)
        market, base_date, source = fetch_market_snapshot(base_date)
        row = market[market["종목코드"] == SELFTEST_TICKER]
        log(f"  [OK] source={source} / 기준일 {base_date} / {len(market)}종목")
        if not row.empty:
            r = row.iloc[0]
            log(f"       {SELFTEST_TICKER} {r['종목명']} 종가={int(r['종가']):,} "
                f"시총={r['시가총액'] / EOK:,.0f}억 주식수={int(r['상장주식수']):,}")
        big = int((market["시가총액"] >= MIN_MARKET_CAP_KRW).sum())
        log(f"       시총 {MIN_MARKET_CAP_KRW / EOK:,.0f}억 이상 {big}종목 (DART 조회 대상 규모)")
        if not market["시장PBR"].notna().any():
            log("  [warn] 이 소스에는 시장PBR이 없어 '시장PBR'·'괴리율(%)'이 공란이 됩니다.")
            log("         계산PBR 스크리닝에는 영향이 없습니다. 괴리율까지 원하면")
            log("         KRX_ID/KRX_PW 환경변수를 설정해 pykrx 경로를 쓰세요.")
    except Exception as exc:
        ok = False
        log(f"  [FAIL] {type(exc).__name__}: {exc}")
        log("       → 주 소스(pykrx)와 대체 소스가 모두 실패했습니다.")
        log("         'Expecting value: line 1 column 1' 은 KRX가 빈 응답을 준 것으로,")
        log("         2026년부터 data.krx.co.kr이 로그인 세션을 요구하기 때문입니다.")
        log("         KRX_ID/KRX_PW 를 설정하거나 네트워크를 확인하세요.")
        log("         (DART 키와는 무관한 단계입니다.)")

    log("")
    log("[진단 2/3] DART API 키 & corpCode 조회")
    api_key = None
    corp_map = {}
    try:
        api_key = get_api_key()
        corp_map = fetch_corp_code_map(api_key)
        log(f"  [OK] 상장사 {len(corp_map)}건 / {SELFTEST_TICKER} -> "
            f"corp_code={corp_map.get(SELFTEST_TICKER)}")
    except SystemExit as exc:
        ok = False
        log(f"  [FAIL] {exc}")
        log("       → 키 오타이거나, 발급 후 이메일 인증이 안 끝났을 수 있습니다.")
        log("         https://opendart.fss.or.kr 에서 키 상태를 확인하세요.")
    except Exception as exc:
        ok = False
        log(f"  [FAIL] {type(exc).__name__}: {exc}")

    log("")
    log("[진단 3/3] DART 재무상태표 조회 & 계정 파싱")
    if not (api_key and corp_map.get(SELFTEST_TICKER) and base_date):
        log("  [SKIP] 앞 단계가 실패해 건너뜁니다.")
        ok = False
    else:
        try:
            periods = build_period_candidates(base_date)
            log(f"  보고서 후보(최신순): {', '.join(p.label for p in periods[:MAX_PERIOD_TRIES])}")
            client = DartClient(api_key)
            res, period, reason = fetch_equity(
                client, corp_map[SELFTEST_TICKER], PeriodResolver(periods))
            if res is None or period is None:
                ok = False
                log(f"  [FAIL] {reason}")
                log("       → 후보 보고서가 아직 미공시일 수 있습니다. "
                    "BASE_DATE를 과거 날짜로 바꿔 다시 시도해 보세요.")
            else:
                equity = res.parent_equity if res.parent_equity is not None else res.equity_total
                fmt = (lambda v: f"{v / EOK:,.0f}억" if v is not None else "없음")
                log(f"  [OK] {period.label} "
                    f"{'연결' if res.fs_div == 'CFS' else '별도'} / "
                    f"자본총계={fmt(res.equity_total)} / "
                    f"지배주주지분={fmt(res.parent_equity)} "
                    f"→ 자기자본 {fmt(equity)} (DART 호출 {client.calls}건)")
        except SystemExit as exc:
            ok = False
            log(f"  [FAIL] {exc}")
        except Exception as exc:
            ok = False
            log(f"  [FAIL] {type(exc).__name__}: {exc}")

    log("")
    log("=" * 72)
    log("진단 결과: 정상 — 전체 실행 가능합니다." if ok
        else "진단 결과: 위에서 [FAIL]로 표시된 첫 단계가 원인입니다.")
    log("=" * 72)
    return ok


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="코스피 밸류 스크리너 1단계")
    p.add_argument("--selftest", action="store_true",
                   help="pykrx/DART 연결과 파싱을 점검하고 종료")
    p.add_argument("--limit", type=int, default=LIMIT_CANDIDATES,
                   help="시총 상위 N종목만 처리 (0=전체). 전체 실행 전 빠른 확인용")
    p.add_argument("--ticker", nargs="*", default=None,
                   help="특정 종목코드만 추적 (시총·우선주 필터 건너뜀, 상세 로그)")
    # 코랩 셀에서 %run 할 때 노트북 인자가 섞여 들어와도 죽지 않도록
    args, _unknown = p.parse_known_args()
    return args


if __name__ == "__main__":
    _args = _parse_args()
    if _args.selftest:
        sys.exit(0 if selftest() else 1)
    run(limit=_args.limit, tickers=_args.ticker)
