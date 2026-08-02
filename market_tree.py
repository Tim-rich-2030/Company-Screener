# -*- coding: utf-8 -*-
"""
시장 지도 — 그날 코스피·코스닥 전 종목의 등락과 업종을 한 장으로 모은다.

트리맵 타일 하나가 종목 하나다. 넓이는 시가총액, 색은 등락률.
같은 데이터를 두 가지로 본다.
  · 상승순 — 오른 순서대로 늘어놓는다. 오늘 뭐가 움직였나
  · 섹터순 — 업종끼리 묶는다. 오늘 어느 판이 움직였나

업종은 KRX 업종지수의 구성종목으로 정한다. 한 종목이 여러 지수에 들어가는데
(예: 은행 ⊂ 금융업 ⊂ 제조업 아님, 하지만 화학 ⊂ 제조업), 그럴 때는 **구성종목이
가장 적은 지수**를 그 종목의 업종으로 본다. 가장 좁은 분류가 가장 구체적이다.
크기·테마로 뽑은 지수(대형주, 코스피200 …)는 업종이 아니므로 먼저 제외한다.

    python market_tree.py                 # 최신 영업일 기준 수집
    python market_tree.py --date 2026-07-31
"""
from __future__ import annotations

import os
import re
import sys
import json
import argparse
import datetime as dt

# 타일로 보여줄 종목 수. 트리맵은 이보다 많아지면 글자가 안 들어간다.
# 업종 집계는 잘라내기 **전** 전 종목으로 낸다.
TOP_N = 240

MARKETS = ("KOSPI", "KOSDAQ")

# 휴장일에 돌렸을 때 며칠까지 거슬러 올라가 볼지. 설·추석 연휴가 가장 길다.
BACKOFF_DAYS = 10

# 업종이 아닌 지수 — 크기·테마·전략으로 뽑은 것들. 이름으로 거른다.
NOT_SECTOR = re.compile(
    r"코스피|코스닥|대형주|중형주|소형주|우량|프리미어|글로벌|배당|가치|성장|"
    r"모멘텀|저변동|로우볼|섹터|지배구조|ESG|탄소|리츠|인프라|고배당|"
    r"\d{2,4}|TOP|KRX|KTOP|F-|Fn"
)
# '제조업'·'금융업' 같은 상위 묶음은 일부러 빼지 않는다. 좁은 지수 우선 규칙이
# 겹침을 이미 해결하고(은행 ⊂ 금융업 이면 은행이 이긴다), 빼버리면 은행·증권·
# 보험 어디에도 안 들어가는 금융주가 '기타' 로 떨어진다. 넓은 이름이라도
# '기타' 보다는 낫다.

STORE_PATH = os.path.join("store", "market_tree.json")
DOCS_PATH = os.path.join("docs", "market_tree.json")


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 수집
# =============================================================================

def _stock():
    """
    pykrx 를 불러온다.

    pykrx 는 import 시점에 KRX 로그인을 시도한다. KRX 가 죽어 있으면 여기서
    ImportError 가 아닌 다른 예외가 튀어나오므로 넓게 잡는다 — 시장 지도 하나
    때문에 지수 수집까지 같이 죽으면 안 된다.
    """
    try:
        from pykrx import stock
        return stock
    except Exception as e:                      # noqa: BLE001
        log(f"::warning::pykrx 를 쓸 수 없습니다 ({type(e).__name__}: {e})")
        return None


def sector_map(stock, date: str, market: str) -> dict:
    """{종목코드: 업종명}. 실패하면 빈 dict — 업종 없이 상승순만 보여주면 된다."""
    try:
        tickers = stock.get_index_ticker_list(date, market=market)
    except Exception as e:                      # noqa: BLE001
        log(f"::warning::{market} 업종지수 목록 실패 ({type(e).__name__}: {e})")
        return {}

    groups = []                                  # [(이름, {종목코드})]
    for idx in tickers:
        try:
            name = stock.get_index_ticker_name(idx)
        except Exception:                        # noqa: BLE001
            continue
        if not name or NOT_SECTOR.search(name):
            continue
        try:
            members = set(stock.get_index_portfolio_deposit_file(idx, date))
        except Exception:                        # noqa: BLE001
            continue
        if members:
            groups.append((name.strip(), members))

    # 좁은 지수부터 배정한다. 먼저 배정된 것을 넓은 지수가 덮어쓰지 못한다.
    groups.sort(key=lambda g: len(g[1]))
    out = {}
    for name, members in groups:
        for code in members:
            out.setdefault(code, name)
    log(f"  {market} 업종 {len(groups)}개 · {len(out)}종목 분류")
    return out


def last_trading_day(stock, start: dt.date, back: int = BACKOFF_DAYS) -> str | None:
    """
    실제로 시세가 있는 가장 가까운 과거 날짜를 찾는다.

    지수 수집기는 기간을 통째로 요청하니 알아서 마지막 거래일로 떨어지지만,
    여기는 하루만 묻는다. 그래서 주말·공휴일에 돌리면 빈 표를 받는다.
    (실제로 일요일 실행에서 이걸로 통째로 실패했다.)
    """
    for i in range(back):
        d = (start - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
        except Exception as e:                   # noqa: BLE001
            log(f"  {d}: 조회 실패 ({type(e).__name__}) — 하루 앞으로")
            continue
        if df is not None and not df.empty:
            if i:
                log(f"  {start:%Y%m%d} 은 휴장 — {d} 로 물러섭니다")
            return d
    return None


def collect(date: str = None) -> dict:
    """그날의 종목별 등락률·시가총액·업종."""
    stock = _stock()
    if stock is None:
        raise SystemExit("pykrx 없이는 시장 지도를 만들 수 없습니다")

    if date is None:
        date = last_trading_day(stock, dt.date.today())
        if date is None:
            raise SystemExit(f"최근 {BACKOFF_DAYS}일 안에 시세가 있는 날이 "
                             "없습니다 — KRX 접속 문제로 보입니다")
    items, used_date = [], None

    for market in MARKETS:
        log(f"[수집] {market} {date}")
        try:
            ohlcv = stock.get_market_ohlcv_by_ticker(date, market=market)
            caps = stock.get_market_cap_by_ticker(date, market=market)
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{market} 시세 실패 ({type(e).__name__}: {e})")
            continue
        if ohlcv is None or ohlcv.empty:
            log(f"::warning::{market} {date} 시세가 비었습니다 (휴장일일 수 있습니다)")
            continue

        sectors = sector_map(stock, date, market)
        cap_col = caps["시가총액"].to_dict() if caps is not None and not caps.empty else {}

        for code, row in ohlcv.iterrows():
            cap = cap_col.get(code)
            close = row.get("종가")
            chg = row.get("등락률")
            # 시총이 없거나 거래가 없던 종목은 타일로 그릴 수 없다. 넓이가 없다.
            if not cap or cap <= 0 or close is None or chg is None:
                continue
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:                    # noqa: BLE001
                name = code
            items.append({
                "code": code,
                "name": name,
                "market": market,
                "sector": sectors.get(code, "기타"),
                "chg": round(float(chg), 2),
                "cap": int(cap),
                "close": int(close),
            })
        used_date = date
        log(f"  {market} {len(ohlcv)}종목 중 {len(items)}건 누적")

    if not items:
        raise SystemExit(f"{date} 시세를 받지 못했습니다 — 휴장일이거나 KRX 접속 실패")

    return {"date": used_date, "source": "pykrx", "items": items}


# =============================================================================
# 집계
# =============================================================================

def sectors_of(items: list) -> list:
    """
    업종별 집계. 등락률은 **시가총액 가중 평균**이다.

    단순 평균을 내면 시총 1000억짜리 종목과 300조짜리 종목이 같은 무게가 되어,
    작은 종목 몇 개가 업종 전체를 위아래로 끌고 간다. 업종 지수가 실제로 움직인
    폭을 보려면 시총으로 눌러야 한다.
    """
    agg = {}
    for it in items:
        s = agg.setdefault(it["sector"], {"name": it["sector"], "cap": 0,
                                          "wsum": 0.0, "n": 0, "up": 0, "down": 0})
        s["cap"] += it["cap"]
        s["wsum"] += it["cap"] * it["chg"]
        s["n"] += 1
        if it["chg"] > 0:
            s["up"] += 1
        elif it["chg"] < 0:
            s["down"] += 1
    out = []
    for s in agg.values():
        out.append({"name": s["name"], "cap": s["cap"], "n": s["n"],
                    "up": s["up"], "down": s["down"],
                    "chg": round(s["wsum"] / s["cap"], 2) if s["cap"] else 0.0})
    out.sort(key=lambda s: -s["chg"])
    return out


def build(raw: dict, top_n: int = TOP_N) -> dict:
    items = raw["items"]
    # 집계는 전 종목으로 먼저. 타일만 시총 상위로 자른다.
    sectors = sectors_of(items)
    breadth = {"up": sum(1 for i in items if i["chg"] > 0),
               "down": sum(1 for i in items if i["chg"] < 0),
               "flat": sum(1 for i in items if i["chg"] == 0),
               "total": len(items)}
    tiles = sorted(items, key=lambda i: -i["cap"])[:top_n]
    tiles.sort(key=lambda i: -i["chg"])          # 상승순
    return {
        "date": raw["date"],
        "source": raw["source"],
        "breadth": breadth,
        "shown": len(tiles),
        "sectors": sectors,
        "items": tiles,
    }


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="시장 지도 (트리맵) 데이터 수집")
    p.add_argument("--date", help="YYYY-MM-DD 또는 YYYYMMDD (기본: 오늘)")
    p.add_argument("--top", type=int, default=TOP_N, help=f"타일 수 (기본 {TOP_N})")
    a = p.parse_args(argv)

    date = a.date.replace("-", "") if a.date else None
    payload = build(collect(date), a.top)
    save(payload)

    b = payload["breadth"]
    log(f"\n{payload['date']} · 상승 {b['up']} / 보합 {b['flat']} / 하락 {b['down']} "
        f"(전체 {b['total']}종목, 타일 {payload['shown']}개)")
    log("업종 상위 5: " + ", ".join(
        f"{s['name']} {s['chg']:+.2f}%" for s in payload["sectors"][:5]))
    log("업종 하위 5: " + ", ".join(
        f"{s['name']} {s['chg']:+.2f}%" for s in payload["sectors"][-5:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
