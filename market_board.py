# -*- coding: utf-8 -*-
"""
증시 현황판 — 1면 대시보드가 쓰는 자료.

두 갈래를 한 파일에 담는다. 둘 다 1면 현황판의 페이지 하나씩이라 같이 받고
같이 배포되어야 한다. 하나만 새것이면 화면 안에서 날짜가 어긋난다.

  · 간밤의 증시(night)
        나스닥 · S&P 500 · 필라델피아 반도체 · 코스피200 선물.
        값과 **전일 대비 포인트·%** 를 함께 낸다.
        장이 열려 있는지 닫혔는지는 여기서 정하지 않는다 — 화면이 시계를
        보고 판단한다. 하루 한 번 받은 파일에 '지금 장중'을 박아 두면
        그 문장은 받은 순간부터 거짓말이 된다.

  · 주요 지표 추세(trend)
        원/달러 · 원/엔 · WTI · 금 · 비트코인의 일별 값. 한 차트에 다섯 선을
        겹쳐 그리려고 **값 자체**를 보낸다. 자릿수가 제각각이라(1,460원 /
        84달러 / 1억 원) 눈금 맞추기는 화면에서 한다.

**짐작으로 긁지 않는다.**
  받을 곳이 확실한 것(FRED·네이버 시장지표)은 바로 받고, 확실하지 않은 것
  (해외 지수·야간선물)은 후보 주소를 차례로 두드린 뒤 **무엇을 두드려 무슨
  답이 왔는지 store/ 에 남긴다.** 답이 온 자료의 열쇠 이름까지 적어 둔다.
  다음 판은 그 기록을 보고 고친다. 로그는 다른 단계 출력에 묻힌다.

못 받은 지표는 값을 비운다. 틀린 데이터가 없는 데이터보다 위험하다.

    python market_board.py            # 전부 수집·저장 (하루 한 번)
    python market_board.py --quick    # 시세만. 추세는 지난 판 그대로 (15분마다)
    python market_board.py --dump     # 후보 주소를 두드려 본 결과만 출력
    python market_board.py --probe    # 선물 심볼 찾기 → store/board_probe.json
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import argparse
import datetime as dt

import requests

from market_macro import (UA, TIMEOUT, flat_headers, fetch_fred, fetch_gold,
                          _txt, TD)

STORE_PATH = os.path.join("store", "market_board.json")
DOCS_PATH = os.path.join("docs", "market_board.json")
# 야간선물 심볼을 찾는 기록. 로그는 다른 단계 출력에 묻힌다 (theme_probe.json 과 같은 자리).
PROBE_PATH = os.path.join("store", "board_probe.json")

DAYS = 180          # 추세 창(달력일). 폰에서 반년이면 흐름이 보인다
PAUSE = 0.2

# =============================================================================
# 간밤의 증시
# =============================================================================
#
# 해외 지수는 네이버 금융 '해외증시' 가 쓰는 자료를 그대로 본다. 심볼은
# 거래소@종목 꼴이다 (NAS@IXIC). 다만 주소 모양이 몇 번 바뀌었으므로
# 후보를 늘어놓고 먼저 답하는 것을 쓴다.
#
# 국내 선물은 받을 곳이 분명하지 않았다. 후보를 두드리고 --probe 로 찾아
# FUT 라는 것을 알아냈다. 그래도 원칙은 같다 — 하나도 답하지 않으면
# **그 줄은 빈 채로 둔다.** 현물 종가를 선물이라고 적으면 다른 숫자다.
IDX_URLS = [
    "https://api.stock.naver.com/index/{sym}/basic",
    "https://api.stock.naver.com/index/{sym}/price?pageSize=2&page=1",
    "https://polling.finance.naver.com/api/realtime/worldstock/index/{sym}",
    "https://polling.finance.naver.com/api/realtime/domestic/index/{sym}",
]

NIGHT = [
    {"key": "nasdaq", "name": "나스닥", "market": "US",
     "syms": [".IXIC", "NAS@IXIC"], "note": "나스닥 종합지수"},
    {"key": "sp500", "name": "S&P 500", "market": "US",
     "syms": [".INX", "SPI@SPX"], "note": "S&P 500 지수"},
    {"key": "sox", "name": "필라델피아 반도체", "market": "US",
     "syms": [".SOX", "SPI@SOX"], "note": "필라델피아 반도체 지수"},
    # 다우는 '간밤의 증시' 목록에 넣지 않는다 (다섯 줄만 보기로 했다). 다만
    # 머리의 지수 띠에는 다우가 있고, 거기 값이 FRED 종가라 하루 늦다.
    # 같은 화면에 나스닥이 두 값으로 나오면 어느 쪽도 못 믿게 되므로,
    # 띠에 쓸 값만 여기서 함께 받아 둔다.
    {"key": "dow", "name": "다우", "market": "US", "in_board": False,
     "syms": [".DJI", "DJI@DJI"], "note": "다우존스 산업평균"},
    # 코스피·코스닥도 '간밤' 이 아니라 지금 장이라 목록에는 안 넣는다. 다만
    # 머리의 지수 띠에 있고 그 값이 하루 한 번 받은 종가라, 장중에 열면
    # 어제 숫자가 오늘 시세인 척한다. 띠에 쓸 값만 여기서 함께 받는다.
    # (probe 가 확인해 준 심볼이다 — KOSPI 6257.45, KOSDAQ 737.35)
    {"key": "코스피", "name": "코스피", "market": "KR", "in_board": False,
     "syms": ["KOSPI"], "note": "코스피 지수"},
    {"key": "코스닥", "name": "코스닥", "market": "KR", "in_board": False,
     "syms": ["KOSDAQ"], "note": "코스닥 지수"},
    # 심볼은 **FUT** 이다. 지어낸 이름을 열 개 두드려도 안 나왔는데,
    # 네이버 KPI200 화면이 쓰는 코드를 그대로 긁으니 한 줄에 있었다.
    #
    #   FUT, KOSDAQ, KOSPI, KPI100, KPI200, KVALUE
    #
    # 같은 시각에 KPI200(현물)은 986.72(-5.74%), FUT 는 993.40(+0.11%) 이었다.
    # 값이 따로 논다 — 현물 종가를 선물이라고 적는 것이 아니라는 뜻이다.
    #
    # 이름은 '야간선물' 이라고 달지 않는다. 이 심볼이 밤에는 야간장을, 낮에는
    # 정규장을 보여 주는 하나의 최근월물 시세이고, 우리는 밤에만 본다는 것을
    # 아직 확인하지 못했다. 확인한 것만 적는다 — 코스피200 선물이다.
    # 응답 원본이 장 시간을 함께 준다 — **야간장은 없다.**
    #
    #   "stockExchangeType": { "startTime": "0900", "endTime": "1530" }
    #
    # 밤새 값이 992.35 에서 한 번도 안 움직인 것과 맞는다. 그래서 이 줄은
    # '간밤' 이 아니라 **어제 국내 정규장** 시세다. 화면에 그렇게 적어 둔다.
    {"key": "k200_fut", "name": "코스피200 선물", "market": "KR_FUT",
     "syms": ["FUT"], "daily": True,
     "note": "코스피200 최근월물 · 국내 정규장 09:00~15:30 (네이버 국내선물)"},
    # 코스닥150 선물은 **뺐다.**
    #
    #   네이버 국내지수 코드에 코스닥 선물이 없다 (위 여섯 개가 전부다).
    #   후보를 세 개 두드려 봤지만 넷 다 빈 답이었다. 없는 것을 매번 열두 번씩
    #   두드릴 이유가 없고, 빈 줄이 자리만 차지할 이유도 없다.
    #   기록은 store/board_probe.json 에 남아 있다 — 나중에 생기면 그때 넣는다.
]

# 야간선물을 어디서 받을 수 있는지는 아직 모른다. 첫 실행이 알려준 것:
#
#   api.stock.naver.com/index/KOSPI200F_NIGHT/basic          → 409
#   polling.finance.naver.com/api/realtime/domestic/index/…  → 200, datas: []
#
# 409 는 '그런 심볼이 없다'는 뜻이지 '그런 주소가 없다'가 아니다. 두 주소 다
# 살아 있고 **심볼만 틀렸다.** 그래서 --probe 로 심볼을 찾는다. 아는 심볼
# (KOSPI)로 먼저 답이 오는지 확인하고, 네이버 화면이 실제로 쓰는 코드를
# 페이지에서 긁어 본다 — 짐작으로 이름을 지어내지 않는다.
PROBE_SYMS = ["KOSPI", "KOSDAQ", "KPI200", "KOSPI200", "KOSPI200F", "K200F",
              "KRDRVFUK2I", "KRDRVFUKQI", "FUT", "KOSPI200FUT",
              "KOSPI200F_NIGHT", "NIGHT_KOSPI200F", "CME_KOSPI200"]
PROBE_PAGES = [
    "https://finance.naver.com/sise/",
    "https://finance.naver.com/sise/sise_index.naver?code=KPI200",
    "https://m.stock.naver.com/domestic/index/KOSPI/total",
]
CODE_TOKEN = re.compile(r"(?:code=|/index/|/futures/|symbol=)([A-Za-z0-9_.@]{2,24})")

# 어느 열쇠에 값이 들어 있는지는 주소마다 다르다. 이름 후보를 늘어놓고 찾는다.
CLOSE_KEYS = ("closePrice", "tradePrice", "currentPrice", "nv", "now", "clpr")
DIFF_KEYS = ("compareToPreviousClosePrice", "changeValue", "cv", "change",
             "compareToPreviousPrice")
RATE_KEYS = ("fluctuationsRatio", "changeRate", "cr", "fluctuationRate",
             "compareRatio")
AT_KEYS = ("localTradedAt", "tradeDate", "localTradedDate", "dt", "aq")

# 장 사이 시간에는 실시간 시세가 등락을 0 으로 준다.
#
#   코스피200 선물 992.35 · 등락 0.00 · at 07:44 KST
#   야간장은 05:00 에 끝났고 정규장은 09:00 에 시작한다. 그 사이라 '이번 장'
#   에서 움직인 폭이 0 인 것이다. 네이버가 틀린 것이 아니라, 우리가 물은 것이
#   '이번 장' 이었다. 우리가 알고 싶은 것은 **당일 등락** 이다.
#
# 그건 일별 시세로 답한다 — 어제 종가와 오늘 종가는 둘 다 사실이고, 그 차이를
# 내는 것은 지어내는 것이 아니다. 실시간이 등락을 주면 그쪽을 쓰고, 0 으로
# 줄 때만 일별로 채운다. 값과 등락을 같은 출처에서 가져와야 서로 어긋나지 않는다.
DAILY_URL = ("https://api.finance.naver.com/siseJson.naver"
             "?symbol={sym}&requestType=1&startTime={start}&endTime={end}"
             "&timeframe=day")
# 응답은 JSON 이 아니라 작은따옴표가 섞인 배열 글이다. 줄 모양만 집는다.
#   ['날짜','시가','고가','저가','종가','거래량','외국인소진율']
#   ["20260803", 992.35, 1000.10, 985.00, 992.35, 123456, 0.00]
DAILY_ROW = re.compile(
    r'\[\s*["\']?(\d{8})["\']?\s*,\s*([\d.]+)\s*,\s*([\d.]+)'
    r'\s*,\s*([\d.]+)\s*,\s*([\d.]+)')

# =============================================================================
# 주요 지표 추세
# =============================================================================
FX_URL = ("https://finance.naver.com/marketindex/exchangeDailyQuote.naver"
          "?marketindexCd={cd}&page={page}")
BASE_COL = re.compile(r"매매\s*기준율")
UPBIT_URL = ("https://api.upbit.com/v1/candles/days"
             "?market=KRW-BTC&count={count}")

TREND = [
    {"key": "usdkrw", "name": "원/달러", "unit": "원", "digits": 1,
     "fx": "FX_USDKRW", "fred": "DEXKOUS", "note": "매매기준율"},
    {"key": "jpykrw", "name": "원/엔", "unit": "원", "digits": 2,
     "fx": "FX_JPYKRW", "note": "100엔당 매매기준율"},
    {"key": "wti", "name": "WTI", "unit": "달러", "digits": 2,
     "fred": "DCOILWTICO", "note": "서부텍사스산 현물"},
    {"key": "gold", "name": "금", "unit": "달러", "digits": 2,
     "gold": True, "note": "국제 금 시세"},
    {"key": "btc", "name": "비트코인", "unit": "원", "digits": 0,
     "upbit": True, "note": "업비트 KRW-BTC 종가"},
]


def log(msg) -> None:
    print(msg, flush=True)


def tonum(v):
    """'1,234.56' · '-12.3' · 1234 → float. 못 읽으면 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def walk(o):
    """중첩된 응답 어디에 들어 있든 dict 를 하나씩 꺼낸다."""
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def first(d: dict, keys) -> object:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def quote_of(body) -> dict:
    """
    응답에서 시세가 든 dict 를 찾는다.

    감싸는 모양이 주소마다 다르다 (그대로 · {"result":…} · [{…}]).
    **종가로 읽을 수 있는 숫자가 있는 첫 dict** 를 시세로 본다.
    """
    for d in walk(body):
        close = tonum(first(d, CLOSE_KEYS))
        if close is None or close <= 0:
            continue
        diff = tonum(first(d, DIFF_KEYS))
        rate = tonum(first(d, RATE_KEYS))
        # 하나만 있으면 나머지는 만들 수 있다. 등락률이 있으면 전일 종가가
        # 나오고, 포인트가 있으면 등락률이 나온다.
        if diff is None and rate is not None and rate != -100:
            prev = close / (1 + rate / 100)
            diff = close - prev
        if rate is None and diff is not None and close != diff:
            rate = diff / (close - diff) * 100
        at = first(d, AT_KEYS)
        return {"raw": {k: v for k, v in list(d.items())[:24]},
                "last": close,
                "diff": None if diff is None else round(diff, 2),
                "rate": None if rate is None else round(rate, 2),
                # 시각은 '2026-08-03T17:15:59-04:00' 로 온다. 24자에서 자르면
                # 시간대가 '-04:0' 으로 잘려 못 읽는 값이 된다.
                "at": None if at is None else str(at)[:32],
                "keys": ",".join(list(d)[:24])}
    return {}


def fetch_daily(sym: str, why: dict = None, name: str = "") -> list:
    """
    일별 종가. [(YYYYMMDD, 종가), ...] 를 날짜순으로.

    실시간 시세가 '이번 장' 기준이라 장 사이에는 등락이 0 이다. 당일 등락은
    어제 종가와 오늘 종가로 낸다 — 둘 다 사실이고, 그 차이를 내는 것은
    지어내는 것이 아니다.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=30)
    url = DAILY_URL.format(sym=sym, start=start.strftime("%Y%m%d"),
                           end=end.strftime("%Y%m%d"))
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Referer": "https://finance.naver.com/"},
                         timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:                               # noqa: BLE001
        if why is not None:
            why[f"{name}/일별"] = f"{type(e).__name__}: {e}"[:140]
        return []
    rows = []
    for day, _o, _h, _l, close in DAILY_ROW.findall(r.text):
        try:
            v = float(close)
        except ValueError:
            continue
        if v > 0:
            rows.append((day, v))
    rows.sort()
    if why is not None and not rows:
        why[f"{name}/일별"] = f"{r.status_code} · {len(r.text)}자 · 줄을 못 읽음"
    return rows


def fetch_quote(spec: dict, why: dict) -> dict:
    """
    후보 주소 × 후보 심볼을 차례로 두드린다.

    성공하면 어느 주소가 답했는지, 실패하면 무슨 답이 왔는지를 남긴다.
    이 기록이 다음 판을 고치는 근거다.
    """
    tried = []
    for sym in spec["syms"]:
        for tpl in IDX_URLS:
            url = tpl.format(sym=sym)
            short = url.split("//", 1)[-1][:70]
            try:
                r = requests.get(url, headers={"User-Agent": UA,
                                               "Referer": "https://m.stock.naver.com/"},
                                 timeout=TIMEOUT)
            except Exception as e:                       # noqa: BLE001
                tried.append(f"{short} → {type(e).__name__}")
                continue
            if r.status_code >= 400:
                tried.append(f"{short} → {r.status_code}")
                continue
            try:
                body = r.json()
            except ValueError:
                tried.append(f"{short} → 200 이지만 JSON 아님({len(r.content)}바이트)")
                continue
            q = quote_of(body)
            if not q:
                tried.append(f"{short} → 200 인데 시세 열쇠 없음 "
                             f"({str(body)[:60]})")
                continue
            # 시각이 시세 안에 없는 응답이 있다. polling 쪽은 바깥에 time 으로
            # 붙여 보낸다 — 어느 장의 값인지 아는 유일한 실마리라 챙긴다.
            if not q.get("at") and isinstance(body, dict) and body.get("time"):
                q["at"] = str(body["time"])[:32]
            why[f"{spec['name']}/출처"] = f"{short} · 열쇠 {q.pop('keys', '')}"
            raw = q.pop("raw", {})
            # **등락이 0 이거나 없으면 원본을 통째로 남긴다.**
            #
            #   코스피200 선물이 992.35 인데 등락은 0.00 으로 왔다. 장 사이
            #   시간이라 네이버가 보합으로 주는 것인지, 우리가 엉뚱한 열쇠를
            #   읽은 것인지, 전일 종가가 다른 이름으로 따로 오는 것인지 —
            #   응답을 봐야 안다. 짐작으로 고치면 없는 숫자를 만들게 된다.
            if q.get("rate") in (None, 0, 0.0):
                why[f"{spec['name']}/원본"] = json.dumps(
                    raw, ensure_ascii=False)[:600]
            return q
    why[spec["name"]] = " | ".join(tried[:6])[:400]
    return {}


# =============================================================================
# 추세 — 값이 확실한 곳에서 받는다
# =============================================================================

def fetch_fx(cd: str, start: dt.date, pages: int = 15, why: dict = None) -> dict:
    """
    네이버 금융 시장지표의 환율 일별 시세에서 **매매기준율** 칸.

    금 표와 같은 두 줄 머리다. 칸 자리를 짐작하지 않고 머리에서 세어 찾는다
    (market_macro.flat_headers 를 그대로 쓴다). 못 찾으면 빈 값을 낸다.
    """
    out, col = {}, None
    for page in range(1, pages + 1):
        try:
            r = requests.get(FX_URL.format(cd=cd, page=page),
                             headers={"User-Agent": UA, "Referer":
                                      "https://finance.naver.com/marketindex/"},
                             timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:                           # noqa: BLE001
            if why is not None:
                why[f"{cd}/{page}쪽"] = f"{type(e).__name__}: {e}"[:140]
            break
        doc = r.content.decode("cp949", "replace")

        if col is None:
            heads = flat_headers(doc)
            hit = [i for i, h in enumerate(heads) if BASE_COL.search(h)]
            if why is not None:
                why[f"{cd}/칸"] = " | ".join(heads)[:200]
            if not hit:
                if why is not None:
                    why[cd] = "'매매기준율' 칸을 못 찾아 값을 내보내지 않습니다"
                return {}
            col = hit[0]

        got = 0
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S):
            tds = [_txt(t) for t in TD.findall(row)]
            if len(tds) <= col:
                continue
            m = re.search(r"(20\d{2})\.(\d{2})\.(\d{2})", tds[0])
            v = re.search(r"([\d,]+(?:\.\d+)?)", tds[col])
            if not m or not v:
                continue
            try:
                d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                val = float(v.group(1).replace(",", ""))
            except ValueError:
                continue
            if d >= start and val > 0:
                out[d.strftime("%Y%m%d")] = val
                got += 1
        if not got:
            break
        time.sleep(PAUSE)
    return out


def fetch_btc(start: dt.date, why: dict = None) -> dict:
    """
    비트코인 — 업비트 일봉. 키가 필요 없고 원화 시세를 그대로 준다.

    달러 시세를 주는 거래소는 대부분 미국 IP 를 막거나 키를 요구한다.
    실행이 도는 자리가 미국이라 그쪽은 조용히 451 을 받는다. 원화로 받고
    **원화라고 적는다.**
    """
    need = (dt.date.today() - start).days + 5
    count = max(2, min(200, need))
    try:
        r = requests.get(UPBIT_URL.format(count=count),
                         headers={"User-Agent": UA, "Accept": "application/json"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:                               # noqa: BLE001
        if why is not None:
            why["비트코인"] = f"{type(e).__name__}: {e}"[:160]
        return {}
    out = {}
    for row in rows if isinstance(rows, list) else []:
        day = str(row.get("candle_date_time_kst", ""))[:10].replace("-", "")
        val = tonum(row.get("trade_price"))
        if re.fullmatch(r"\d{8}", day) and val and val > 0:
            out[day] = val
    if why is not None:
        why["비트코인/건수"] = f"{len(out)}일"
    return out


def load_prev(path: str = DOCS_PATH) -> dict:
    """지난 판. --quick 이 추세를 그대로 물려받을 때 쓴다."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def collect(days: int = DAYS, quick: bool = False) -> dict:
    """
    quick=True 면 **시세만** 새로 받고 추세는 지난 판을 그대로 쓴다.

    추세는 일별 값이라 15분마다 다시 받을 이유가 없다. 그런데 그 한 번이
    금 15쪽 + 환율 30쪽 + FRED + 업비트로 쉰 번쯤 두드린다. 15분마다 그러면
    하루에 네이버를 오천 번 두드리게 된다 — 우리가 필요해서가 아니라
    코드가 그렇게 생겨서. 자주 도는 판은 시세 다섯 줄만 받는다.
    """
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    why, failed = {}, []

    night = []
    for spec in NIGHT:
        q = fetch_quote(spec, why)
        row = {"key": spec["key"], "name": spec["name"],
               "market": spec["market"], "note": spec["note"],
               "in_board": spec.get("in_board", True),
               "last": q.get("last"), "diff": q.get("diff"),
               "rate": q.get("rate"), "at": q.get("at")}

        # 장 사이라 등락이 0 으로 온 줄은 일별 종가로 채운다. **값과 등락을
        # 같은 출처에서 가져온다** — 실시간 값에 일별 등락을 붙이면 992.35 가
        # 왜 -5% 인지 설명할 수 없는 줄이 된다.
        if spec.get("daily") and row["last"] is not None and not row["rate"]:
            days = fetch_daily(spec["syms"][0], why, spec["name"])
            if len(days) >= 2:
                (pd, pv), (ld, lv) = days[-2], days[-1]
                row.update({"last": lv, "diff": round(lv - pv, 2),
                            "rate": round((lv / pv - 1) * 100, 2) if pv else None,
                            "at": ld})
                why[f"{spec['name']}/일별"] = f"{ld} {lv} ← {pd} {pv}"
        if row["last"] is None:
            failed.append(spec["name"])
            if spec.get("drop_if_missing"):
                log(f"  {spec['name']}: 없음 — 줄째로 뺍니다")
                continue
            log(f"  {spec['name']}: 없음")
        else:
            log(f"  {spec['name']}: {row['last']} "
                f"({row['diff']}, {row['rate']}%)")
        night.append(row)

    if quick:
        prev = load_prev()
        trend = prev.get("trend", [])
        why["추세"] = f"지난 판 그대로 ({len(trend)}개, --quick)"
        log(f"  추세: 지난 판 그대로 {len(trend)}개")
        return {"as_of": today.strftime("%Y%m%d"),
                "fetched_at": dt.datetime.now(dt.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "days": prev.get("days", days), "night": night, "trend": trend,
                "failed": failed, "why": why}

    trend = []
    for spec in TREND:
        pts = {}
        try:
            if spec.get("fx"):
                pts = fetch_fx(spec["fx"], start, why=why)
            elif spec.get("gold"):
                # 한 쪽에 열흘쯤 들어 있다. 반년을 채우려면 열다섯 쪽쯤 봐야 한다
                # — 기본값(6쪽)으로 두면 석 달치만 와서 선이 짧게 끊긴다.
                pts = fetch_gold(start, pages=15, why=why)
            elif spec.get("upbit"):
                pts = fetch_btc(start, why)
            elif spec.get("fred"):
                pts = fetch_fred(spec["fred"], start)
        except Exception as e:                           # noqa: BLE001
            why[spec["name"]] = f"{type(e).__name__}: {e}"[:160]
            pts = {}
        # 네이버가 막히면 FRED 로 물러선다. 같은 매매기준율이라 이름은 그대로 둔다.
        if not pts and spec.get("fred") and not spec.get("gold"):
            try:
                pts = fetch_fred(spec["fred"], start)
                if pts:
                    why[f"{spec['name']}/대용"] = f"FRED {spec['fred']}"
            except Exception as e:                       # noqa: BLE001
                why[f"{spec['name']}/대용"] = f"{type(e).__name__}: {e}"[:120]

        item = {k: spec[k] for k in ("key", "name", "unit", "digits", "note")}
        if not pts:
            failed.append(spec["name"])
            log(f"  {spec['name']} 추세: 없음")
            item.update({"points": [], "last": None})
            trend.append(item)
            continue
        days_sorted = sorted(pts)
        item.update({"points": [[d, pts[d]] for d in days_sorted],
                     "last": pts[days_sorted[-1]],
                     "last_date": days_sorted[-1]})
        trend.append(item)
        log(f"  {spec['name']} 추세: {len(pts)}일치 · 최근 "
            f"{pts[days_sorted[-1]]}{spec['unit']}")

    return {"as_of": today.strftime("%Y%m%d"),
            "fetched_at": dt.datetime.now(dt.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days": days, "night": night, "trend": trend,
            "failed": failed, "why": why}


def save(payload: dict) -> None:
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    slim = {k: v for k, v in payload.items() if k != "why"}
    os.makedirs(os.path.dirname(DOCS_PATH) or ".", exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (진단 포함), {DOCS_PATH}")


def dump() -> int:
    """후보 주소를 두드려 본 결과만 본다. 저장하지 않는다."""
    for spec in NIGHT:
        log(f"\n===== {spec['name']}")
        for sym in spec["syms"]:
            for tpl in IDX_URLS:
                url = tpl.format(sym=sym)
                try:
                    r = requests.get(url, headers={"User-Agent": UA},
                                     timeout=TIMEOUT)
                except Exception as e:                   # noqa: BLE001
                    log(f"  {url[:78]} → {type(e).__name__}")
                    continue
                head = r.text[:110].replace("\n", " ")
                log(f"  {url[:78]} → {r.status_code} · {len(r.content)}바이트 · {head}")
    return 0


def probe() -> int:
    """
    야간선물 심볼을 **찾는다.** 지어내지 않는다.

    두 가지를 한다.
      1. 아는 심볼(KOSPI)부터 두드려, 국내 지수 주소가 실제로 답하는 모양을 본다.
         그 모양을 알아야 답이 온 것과 빈 답을 가릴 수 있다.
      2. 네이버 화면이 쓰는 코드를 페이지에서 그대로 긁는다. 우리가 상상한
         이름이 아니라 네이버가 쓰는 이름이어야 한다.
    """
    found, tried = {}, []
    log("===== 1. 후보 심볼")
    for sym in PROBE_SYMS:
        for tpl in IDX_URLS:
            url = tpl.format(sym=sym)
            short = url.split("//")[-1][:56]
            try:
                r = requests.get(url, headers={"User-Agent": UA,
                                               "Referer": "https://m.stock.naver.com/"},
                                 timeout=TIMEOUT)
            except Exception as e:                       # noqa: BLE001
                tried.append(f"{sym} · {short} · {type(e).__name__}")
                log(f"  {sym:18} {short:56} {type(e).__name__}")
                continue
            body, mark = None, ""
            try:
                body = r.json()
            except ValueError:
                mark = "JSON 아님"
            if body is not None:
                q = quote_of(body)
                if q:
                    mark = f"시세 {q['last']}"
                    found.setdefault(sym, []).append({"url": short,
                                                      "last": q["last"],
                                                      "rate": q.get("rate")})
                else:
                    mark = f"빈 답 {str(body)[:60]}"
            tried.append(f"{sym} · {short} · {r.status_code} {mark}")
            log(f"  {sym:18} {short:56} {r.status_code} {mark}")
            time.sleep(PAUSE)

    log("\n===== 2. 네이버 화면이 쓰는 코드")
    pages = {}
    for page in PROBE_PAGES:
        try:
            r = requests.get(page, headers={"User-Agent": UA}, timeout=TIMEOUT)
            doc = r.content.decode("cp949", "replace")
        except Exception as e:                           # noqa: BLE001
            pages[page] = {"오류": f"{type(e).__name__}: {e}"[:120]}
            log(f"  {page} → {type(e).__name__}")
            continue
        codes = sorted(set(CODE_TOKEN.findall(doc)))
        # 선물·야간이라는 낱말이 실제로 이 화면에 있는지도 함께 본다.
        words = [w for w in ("야간", "선물", "F_NIGHT", "futures") if w in doc]
        pages[page] = {"status": r.status_code, "코드": codes[:80], "낱말": words}
        log(f"  {page} → {r.status_code} · 코드 {len(codes)}개 · 낱말 {words}")
        log("      " + ", ".join(codes[:60]))

    # 로그는 다른 단계 출력에 묻힌다. 답은 파일로 남긴다.
    os.makedirs(os.path.dirname(PROBE_PATH) or ".", exist_ok=True)
    with open(PROBE_PATH, "w", encoding="utf-8") as f:
        json.dump({"찾은 심볼": found, "두드린 것": tried, "화면": pages},
                  f, ensure_ascii=False, indent=1)
    log(f"\n[저장] {PROBE_PATH} — 시세가 온 심볼 {len(found)}개")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="증시 현황판 — 간밤의 증시·주요 지표")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--quick", action="store_true",
                   help="시세만 받고 추세는 지난 판 그대로 (자주 도는 판)")
    p.add_argument("--dump", action="store_true")
    p.add_argument("--probe", action="store_true",
                   help="야간선물 심볼을 찾는다 (저장하지 않음)")
    a = p.parse_args(argv)
    if a.dump:
        return dump()
    if a.probe:
        return probe()

    payload = collect(a.days, quick=a.quick)
    save(payload)
    if payload["failed"]:
        log(f"::warning::받지 못한 것: {', '.join(payload['failed'])} "
            f"— 그 칸은 비워 둡니다 (store/market_board.json 의 why 참고)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
