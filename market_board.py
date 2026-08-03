# -*- coding: utf-8 -*-
"""
증시 현황판 — 1면 대시보드가 쓰는 자료.

두 갈래를 한 파일에 담는다. 둘 다 1면 현황판의 페이지 하나씩이라 같이 받고
같이 배포되어야 한다. 하나만 새것이면 화면 안에서 날짜가 어긋난다.

  · 간밤의 증시(night)
        나스닥 · S&P 500 · 필라델피아 반도체 · 코스피200 야간선물 ·
        코스닥150 야간선물. 값과 **전일 대비 포인트·%** 를 함께 낸다.
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

    python market_board.py            # 수집·저장
    python market_board.py --dump     # 후보 주소를 두드려 본 결과만 출력
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
# 야간선물은 받을 곳이 분명하지 않다. 후보를 두드려 보고, 하나도 답하지
# 않으면 **그 줄은 빈 채로 둔다** — 코스피200 정규장 종가를 야간선물이라고
# 적으면 그건 다른 숫자다.
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
    {"key": "k200_night", "name": "코스피200 야간선물", "market": "KR_NIGHT",
     "syms": ["KOSPI200F_NIGHT", "CME_KOSPI200", "KPI200F", "K200F"],
     "note": "코스피200 야간선물"},
    {"key": "kq150_night", "name": "코스닥150 야간선물", "market": "KR_NIGHT",
     "syms": ["KOSDAQ150F_NIGHT", "KQ150F", "KOSDAQ150F"],
     "note": "코스닥150 야간선물"},
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
        return {"last": close,
                "diff": None if diff is None else round(diff, 2),
                "rate": None if rate is None else round(rate, 2),
                # 시각은 '2026-08-03T17:15:59-04:00' 로 온다. 24자에서 자르면
                # 시간대가 '-04:0' 으로 잘려 못 읽는 값이 된다.
                "at": None if at is None else str(at)[:32],
                "keys": ",".join(list(d)[:10])}
    return {}


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
            why[f"{spec['name']}/출처"] = f"{short} · 열쇠 {q.pop('keys', '')}"
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


def collect(days: int = DAYS) -> dict:
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
        if row["last"] is None:
            failed.append(spec["name"])
            log(f"  {spec['name']}: 없음")
        else:
            log(f"  {spec['name']}: {row['last']} "
                f"({row['diff']}, {row['rate']}%)")
        night.append(row)

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
    log("===== 1. 후보 심볼")
    for sym in PROBE_SYMS:
        for tpl in IDX_URLS:
            url = tpl.format(sym=sym)
            try:
                r = requests.get(url, headers={"User-Agent": UA,
                                               "Referer": "https://m.stock.naver.com/"},
                                 timeout=TIMEOUT)
            except Exception as e:                       # noqa: BLE001
                log(f"  {sym:18} {url.split('//')[-1][:56]:56} {type(e).__name__}")
                continue
            body, mark = None, ""
            try:
                body = r.json()
            except ValueError:
                mark = "JSON 아님"
            if body is not None:
                q = quote_of(body)
                mark = f"시세 {q['last']}" if q else f"빈 답 {str(body)[:60]}"
            log(f"  {sym:18} {url.split('//')[-1][:56]:56} {r.status_code} {mark}")
            time.sleep(PAUSE)

    log("\n===== 2. 네이버 화면이 쓰는 코드")
    for page in PROBE_PAGES:
        try:
            r = requests.get(page, headers={"User-Agent": UA}, timeout=TIMEOUT)
            doc = r.content.decode("cp949", "replace")
        except Exception as e:                           # noqa: BLE001
            log(f"  {page} → {type(e).__name__}")
            continue
        codes = sorted(set(CODE_TOKEN.findall(doc)))
        log(f"  {page} → {r.status_code} · 코드 {len(codes)}개")
        log("      " + ", ".join(codes[:60]))
        # 선물·야간이라는 낱말이 실제로 이 화면에 있는지도 함께 본다.
        for word in ("야간", "선물", "F_NIGHT", "futures"):
            if word in doc:
                log(f"      '{word}' 있음")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="증시 현황판 — 간밤의 증시·주요 지표")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--dump", action="store_true")
    p.add_argument("--probe", action="store_true",
                   help="야간선물 심볼을 찾는다 (저장하지 않음)")
    a = p.parse_args(argv)
    if a.dump:
        return dump()
    if a.probe:
        return probe()

    payload = collect(a.days)
    save(payload)
    if payload["failed"]:
        log(f"::warning::받지 못한 것: {', '.join(payload['failed'])} "
            f"— 그 칸은 비워 둡니다 (store/market_board.json 의 why 참고)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
