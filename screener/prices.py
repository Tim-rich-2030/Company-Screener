# -*- coding: utf-8 -*-
"""
분기말 주가 스냅샷.

PBR·PER은 '그때의 주가'로 계산해야 시계열이 의미를 갖는다. 2023년 3분기 실적을
오늘 주가로 나누면 그건 2023년의 밸류에이션이 아니다. 그래서 각 분기말 종가를
그 시점 값으로 굳혀 저장한다.

주가 소스는 두 겹이다.
  1) 네이버 차트 (fchart.stock.naver.com) — 종목당 한 번 호출로 과거 수천 일치.
     과거 분기를 소급해 채울 수 있는 유일한 경로다.
  2) FDR 일자별 상장종목 캐시 — 최근 몇 달치만 있지만 KRX 로그인이 필요 없고
     상장주식수까지 함께 준다. 네이버가 막힌 환경의 대비책.

분기말이 휴장일이면 직전 영업일 종가를 쓴다(최대 10일 되감기).

주의: 여기서 계산하는 과거 시가총액은 '그 분기말에 시장이 매긴 값'이다. 그 시점에는
아직 실적이 공시되기 전이므로, "그때 이 PER로 살 수 있었다"는 뜻이 아니다.
실적 발표 시점 기준 밸류에이션이 필요하면 공시일 주가를 따로 받아야 한다.
"""
from __future__ import annotations

import io
import re
import datetime as dt

import requests

from .store import quarter_end_date, parse_quarter

NAVER_CHART = ("https://fchart.stock.naver.com/sise.nhn"
               "?timeframe=day&count={count}&requestType=0&symbol={code}")
FDR_CACHE = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
             "refs/heads/master/data/listing/krx/{date}.csv")

ITEM_RE = re.compile(r'data="(\d{8})\|([\d.]*)\|([\d.]*)\|([\d.]*)\|([\d.]*)\|')
MAX_BACKTRACK_DAYS = 10
TIMEOUT = 20


def fetch_naver_closes(code: str, count: int = 4000) -> dict:
    """{'YYYYMMDD': 종가}. 실패하면 빈 dict."""
    try:
        resp = requests.get(NAVER_CHART.format(count=count, code=code), timeout=TIMEOUT)
        resp.raise_for_status()
        text = resp.content.decode("euc-kr", "replace")
    except Exception:
        return {}
    out = {}
    for ymd, _o, _h, _l, close in ITEM_RE.findall(text):
        try:
            out[ymd] = float(close)
        except ValueError:
            continue
    return out


_fdr_cache: dict[str, dict] = {}


def fetch_fdr_day(date_iso: str) -> dict:
    """{'종목코드': (종가, 상장주식수)} — 해당 일자 스냅샷. 없으면 빈 dict."""
    if date_iso in _fdr_cache:
        return _fdr_cache[date_iso]
    out = {}
    try:
        resp = requests.get(FDR_CACHE.format(date=date_iso), timeout=TIMEOUT)
        if resp.status_code == 200 and resp.content:
            import pandas as pd
            df = pd.read_csv(io.BytesIO(resp.content), dtype={"Code": str})
            for _, row in df.iterrows():
                out[str(row["Code"]).zfill(6)] = (float(row["Close"]), float(row["Stocks"]))
    except Exception:
        pass
    _fdr_cache[date_iso] = out
    return out


def close_on_or_before(closes: dict, target: dt.date) -> tuple[str, float] | None:
    """분기말이 휴장이면 직전 영업일로 되감는다."""
    cur = target
    for _ in range(MAX_BACKTRACK_DAYS + 1):
        ymd = cur.strftime("%Y%m%d")
        if ymd in closes:
            return ymd, closes[ymd]
        cur -= dt.timedelta(days=1)
    return None


def fill_quarter_prices(record: dict, today: dt.date = None) -> int:
    """
    주가가 비어 있는 분기를 채운다. 반환: 채운 분기 수.
    이미 채워진 분기는 건드리지 않는다(과거 스냅샷은 불변).
    """
    from .store import missing_price_quarters, set_price

    today = today or dt.date.today()
    missing = missing_price_quarters(record)
    if not missing:
        return 0

    code = record["code"]
    closes = fetch_naver_closes(code)
    source = "naver" if closes else ""
    filled = 0

    for qkey in missing:
        parsed = parse_quarter(qkey)
        if not parsed:
            continue
        end = quarter_end_date(*parsed)
        if end > today:                     # 아직 끝나지 않은 분기
            continue

        shares = (record["quarters"].get(qkey) or {}).get("상장주식수")
        hit = close_on_or_before(closes, end) if closes else None

        if hit is None:
            # 네이버가 막혔거나 데이터가 없으면 FDR 일자별 캐시로 대체
            cur, found = end, None
            for _ in range(MAX_BACKTRACK_DAYS + 1):
                day = fetch_fdr_day(cur.isoformat())
                if code in day:
                    found = (cur.strftime("%Y%m%d"), day[code])
                    break
                cur -= dt.timedelta(days=1)
            if found is None:
                continue
            ymd, (close, fdr_shares) = found
            shares = shares or fdr_shares
            source = "fdr-cache"
        else:
            ymd, close = hit

        if set_price(record, qkey, close, shares, ymd, source or "unknown"):
            filled += 1
    return filled
