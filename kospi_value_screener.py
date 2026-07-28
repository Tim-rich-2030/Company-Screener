#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
코스피 밸류 스크리너 - 1단계 (자체 계산 PBR)
============================================

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

주의 (해석상의 한계)
    * 금융지주 / 은행 / 보험 / 증권은 자본 구조와 규제자본(BIS비율, RBC/K-ICS 등)의
      성격이 일반 제조업과 완전히 다르다. 자산 대부분이 금융자산이라 장부가치가
      시가에 가깝게 평가되고, 대손충당금·보험부채 할인율 가정에 따라 자기자본이
      크게 흔들린다. 따라서 "PBR 1배 미만 = 저평가"라는 해석을 그대로 적용하면 안 되며,
      본 스크리너의 결과에서 금융업종은 별도의 잣대로 다시 검토해야 한다.
    * 우선주가 상장된 종목은 pykrx 시가총액/상장주식수가 보통주 기준이라
      계산PBR이 실제보다 과소 계산된다. 해당 종목은 '우선주존재' 플래그로 표시한다.
    * 2단계(영업이익 5년/3년 연속 흑자 필터)는 이 파일에 포함하지 않는다.

실행
    코랩:  이 파일 전체를 셀에 붙여넣고 실행하거나 `%run kospi_value_screener.py`
    로컬:  export DART_API_KEY=... && python kospi_value_screener.py
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# =============================================================================
# 기준값 상수 (여기만 고치면 됨)
# =============================================================================

MARKET = "KOSPI"                    # pykrx 시장 구분
BASE_DATE = ""                      # 기준일자 "YYYYMMDD". 빈 문자열이면 오늘 기준
MIN_MARKET_CAP_KRW = 500_000_000_000   # 시가총액 하한: 5,000억원
MAX_CALC_PBR = 1.0                  # 계산PBR 상한 (이하만 통과)
MIN_CALC_PBR = 0.0                  # 계산PBR 하한 (초과만 통과 = 자본잠식/음수 제외)

DART_SLEEP_SEC = 0.12               # DART 호출 간 대기 (분당 호출 제한 회피)
DART_TIMEOUT_SEC = 20               # DART 요청 타임아웃
DART_MAX_RETRY = 3                  # 네트워크 오류 시 재시도 횟수
MAX_PERIOD_TRIES = 4                # 한 종목당 시도할 보고서 후보 개수 상한
UNAVAILABLE_STRIKES = 12            # 특정 보고서가 N회 연속 실패(성공 0회)하면 미공시로 보고 건너뜀

OUTPUT_CSV = "kospi_value_screener_stage1.csv"
EXCLUDED_CSV = "kospi_value_screener_stage1_excluded.csv"   # 제외 사유 상세 (빈 문자열이면 저장 안 함)

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

def log(msg: str) -> None:
    print(msg, flush=True)


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


def get_api_key() -> str:
    """DART API 키를 환경변수 또는 입력창에서 받는다. 하드코딩 금지."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        log("[setup] DART_API_KEY 환경변수 사용")
        return key

    # 코랩 시크릿(userdata) 지원
    try:
        from google.colab import userdata  # type: ignore
        key = (userdata.get("DART_API_KEY") or "").strip()
        if key:
            log("[setup] 코랩 시크릿(DART_API_KEY) 사용")
            return key
    except Exception:
        pass

    from getpass import getpass
    key = getpass("OpenDART API key를 입력하세요 (화면에 표시되지 않음): ").strip()
    if not key:
        raise SystemExit("DART API 키가 없어 종료합니다.")
    return key


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
    # 최후 수단: 데이터가 나올 때까지 하루씩 되감기
    cur = dt.datetime.strptime(date, "%Y%m%d").date()
    for _ in range(10):
        ymd = cur.strftime("%Y%m%d")
        try:
            if not stock.get_market_cap_by_ticker(ymd, market=MARKET).empty:
                return ymd
        except Exception:
            pass
        cur -= dt.timedelta(days=1)
    return date


# =============================================================================
# 1) pykrx 시장 데이터
# =============================================================================

def fetch_market_snapshot(base_date: str) -> pd.DataFrame:
    """코스피 전 종목의 종가/시가총액/상장주식수/시장PBR/종목명."""
    log(f"[1/5] pykrx {MARKET} 시장 데이터 수집 (기준일 {base_date})")

    cap = stock.get_market_cap_by_ticker(base_date, market=MARKET)
    if cap is None or cap.empty:
        raise SystemExit(f"{base_date} 기준 {MARKET} 시가총액 데이터를 받지 못했습니다.")
    cap = cap.rename(columns={"종가": "종가", "시가총액": "시가총액", "상장주식수": "상장주식수"})

    fund = stock.get_market_fundamental_by_ticker(base_date, market=MARKET)
    if fund is None or fund.empty:
        log("  [warn] 시장 PBR(fundamental) 데이터를 받지 못해 시장PBR을 결측 처리합니다.")
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

    # pykrx는 결측 PBR을 0으로 채워 내려주므로 결측으로 되돌린다.
    df["시장PBR"] = pd.to_numeric(df["시장PBR"], errors="coerce")
    df.loc[df["시장PBR"] <= 0, "시장PBR"] = float("nan")

    log(f"      전 종목 {len(df)}개 수집")
    return df


# 우선주 종목명은 '...우', '...우B', '...2우B', '...3우C' 형태로 끝난다.
PREF_NAME_RE = re.compile(r"\d?우[A-Za-z]?$")


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
    log("[2/5] DART 기업 고유번호(corpCode) 내려받는 중")
    resp = requests.get(f"{DART_BASE}/corpCode.xml",
                        params={"crtfc_key": api_key}, timeout=60)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        head = resp.content[:400].decode("utf-8", "replace")
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

    def _dead(self, p: Period) -> bool:
        return self.success[p] == 0 and self.fail[p] >= UNAVAILABLE_STRIKES

    def order(self) -> list[Period]:
        alive = [p for p in self.candidates if not self._dead(p)]
        return (alive or self.candidates)[:MAX_PERIOD_TRIES]

    def mark(self, p: Period, ok: bool) -> None:
        (self.success if ok else self.fail)[p] += 1
        if not ok and self._dead(p) and p not in self._announced:
            self._announced.add(p)
            log(f"      [info] {p.label} 보고서는 아직 미공시로 판단, 이후 조회에서 제외")


class DartClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.calls = 0

    def get_json(self, endpoint: str, params: dict) -> dict | None:
        params = {"crtfc_key": self.api_key, **params}
        for attempt in range(DART_MAX_RETRY):
            try:
                self.calls += 1
                r = self.session.get(f"{DART_BASE}/{endpoint}",
                                     params=params, timeout=DART_TIMEOUT_SEC)
                time.sleep(DART_SLEEP_SEC)          # 호출 제한 회피
                r.raise_for_status()
                return r.json()
            except Exception:
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
    last_reason = "조회 가능한 보고서 없음"
    for period in resolver.order():
        got_period = False
        for fs_div in ("CFS", "OFS"):
            payload = client.get_json("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(period.year),
                "reprt_code": period.reprt_code,
                "fs_div": fs_div,
            })
            if payload is None:
                last_reason = "DART 호출 실패(네트워크/타임아웃)"
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
    return None, None, last_reason


# =============================================================================
# 4~5) 계산 / 필터 / 출력
# =============================================================================

@dataclass
class Excluded:
    rows: list[dict] = field(default_factory=list)

    def add(self, code: str, name: str, stage: str, reason: str, verbose: bool = True) -> None:
        self.rows.append({"종목코드": code, "종목명": name, "단계": stage, "사유": reason})
        if verbose:
            log(f"      [skip] {code} {name}: {reason}")

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=["종목코드", "종목명", "단계", "사유"])


def run() -> pd.DataFrame:
    api_key = get_api_key()
    base_date = resolve_base_date(BASE_DATE)
    excluded = Excluded()

    # --- 1) 시장 데이터 ---------------------------------------------------
    market = fetch_market_snapshot(base_date)
    name_by_code = dict(zip(market["종목코드"], market["종목명"]))
    pref_map = build_preferred_map(list(market["종목코드"]), name_by_code)

    # --- 2) 시가총액 필터 -------------------------------------------------
    total_listed = len(market)
    cand = market[market["시가총액"] >= MIN_MARKET_CAP_KRW].copy()
    dropped_cap = total_listed - len(cand)
    log(f"[3/5] 시가총액 {MIN_MARKET_CAP_KRW / EOK:,.0f}억 이상 필터: "
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

    # --- 3) DART ----------------------------------------------------------
    corp_map = fetch_corp_code_map(api_key)
    periods = build_period_candidates(base_date)
    if not periods:
        raise SystemExit("기준일 기준으로 조회 가능한 보고서 후보가 없습니다.")
    log(f"      보고서 후보(최신순): {', '.join(p.label for p in periods[:6])} ...")

    client = DartClient(api_key)
    resolver = PeriodResolver(periods)
    records: list[dict] = []

    log(f"[4/5] DART 재무상태표 조회 ({len(cand)}종목)")
    for _, row in tqdm(list(cand.iterrows()), total=len(cand), desc="DART", unit="종목"):
        code, name = row["종목코드"], row["종목명"]
        corp_code = corp_map.get(code)
        if not corp_code:
            excluded.add(code, name, "DART매핑", "DART corp_code를 찾지 못함")
            continue

        res, period, reason = fetch_equity(client, corp_code, resolver)
        if res is None or period is None:
            excluded.add(code, name, "DART조회", reason)
            continue

        # 4) 자기자본 = 지배주주지분 우선, 없으면 자본총계
        equity = res.parent_equity if res.parent_equity is not None else res.equity_total
        equity_basis = "지배주주지분" if res.parent_equity is not None else "자본총계"
        if equity is None:
            excluded.add(code, name, "재무결측", "자기자본 값 없음")
            continue
        if equity <= 0:
            excluded.add(code, name, "자본잠식", f"자기자본 {equity / EOK:,.0f}억 <= 0")
            continue

        shares = row["상장주식수"]
        mcap = row["시가총액"]
        if not shares or shares <= 0:
            excluded.add(code, name, "결측", "상장주식수 없음")
            continue
        if not mcap or mcap <= 0:
            excluded.add(code, name, "결측", "시가총액 없음")
            continue

        bps = equity / shares
        calc_pbr = mcap / equity
        mkt_pbr = row["시장PBR"]
        gap = ((calc_pbr - mkt_pbr) / mkt_pbr * 100) if pd.notna(mkt_pbr) and mkt_pbr > 0 else float("nan")
        if pd.isna(gap):
            log(f"      [warn] {code} {name}: 시장PBR 결측 -> 괴리율 계산 불가(결과에는 포함)")

        records.append({
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
        })

    # --- 5) PBR 필터 & 정렬 ----------------------------------------------
    df = pd.DataFrame(records)
    log(f"[5/5] 계산 완료 {len(df)}종목 -> PBR 필터 "
        f"({MIN_CALC_PBR} < 계산PBR <= {MAX_CALC_PBR})")
    if not df.empty:
        fail = df[~((df["계산PBR"] > MIN_CALC_PBR) & (df["계산PBR"] <= MAX_CALC_PBR))]
        for _, r in fail.iterrows():
            excluded.add(r["종목코드"], r["종목명"], "PBR필터",
                         f"계산PBR {r['계산PBR']}", verbose=False)
        df = df[(df["계산PBR"] > MIN_CALC_PBR) & (df["계산PBR"] <= MAX_CALC_PBR)]
        df = df.sort_values("계산PBR", ascending=True).reset_index(drop=True)

    # --- 저장 & 요약 -------------------------------------------------------
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    exc_df = excluded.summary()
    if EXCLUDED_CSV:
        exc_df.to_csv(EXCLUDED_CSV, index=False, encoding="utf-8-sig")

    log("")
    log("=" * 72)
    log(f"기준일자          : {base_date}")
    log(f"전체 상장종목     : {total_listed}")
    log(f"시총 필터 통과    : {len(cand)} (하한 {MIN_MARKET_CAP_KRW / EOK:,.0f}억)")
    log(f"DART 호출 수      : {client.calls}")
    log(f"최종 통과 종목    : {len(df)}")
    log(f"제외 종목 수      : {len(exc_df)}")
    if not exc_df.empty:
        log("제외 사유별 집계  :")
        for stage, cnt in exc_df["단계"].value_counts().items():
            log(f"  - {stage}: {cnt}")
    log(f"결과 CSV          : {OUTPUT_CSV}")
    if EXCLUDED_CSV:
        log(f"제외 상세 CSV     : {EXCLUDED_CSV}")
    if not df.empty and (df["우선주존재"] == "Y").any():
        n = int((df["우선주존재"] == "Y").sum())
        log(f"[note] 우선주 상장 종목 {n}개 포함 — 보통주 시총만 반영되어 "
            f"계산PBR이 과소 계산됨 (우선주존재=Y)")
    log("[note] 금융지주/은행/보험/증권은 자본 구조가 달라 PBR 1배 미만을 "
        "그대로 저평가로 해석하면 안 됨")
    log("=" * 72)

    if not df.empty:
        with pd.option_context("display.max_rows", 50, "display.width", 200):
            print(df.head(30).to_string(index=False))

    # 코랩이면 결과 파일 자동 다운로드 제안
    try:
        from google.colab import files  # type: ignore
        files.download(OUTPUT_CSV)
    except Exception:
        pass

    return df


if __name__ == "__main__":
    run()
