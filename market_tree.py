# -*- coding: utf-8 -*-
"""
시장 지도 — 그날 코스피·코스닥 종목의 등락과 업종을 한 장으로 모은다.

트리맵 타일 하나가 종목 하나다. 넓이는 시가총액, 색은 등락률.
같은 데이터를 세 가지로 본다.
  · 코스피 / 코스닥 — 시장별로 나눠서
  · 섹터순          — 업종끼리 묶어서

업종은 **KRX 공식 업종분류**(get_market_sector_classifications)를 그대로 쓴다.
한 종목에 업종이 하나씩 붙은 진짜 분류표다.

    처음엔 업종지수 60여 개의 구성종목을 받아 '가장 좁은 지수'로 업종을
    추론했다. 결과는 그럴듯했지만 추론은 추론이다 — 지수에 안 들어간 종목은
    '기타'로 떨어지고, 지수 구성이 바뀌면 업종도 따라 흔들린다. 게다가 지수마다
    한 번씩, 시장당 30번 넘게 KRX 를 두드렸다. 공식 분류표는 **시장당 한 번**에
    종목명·업종·종가·등락률·시가총액을 다 준다.

제외 대상 (EXCLUDE_UNDER, 관리종목, 투자주의환기종목)은 아래 상수와
market_flags.py 에서 정한다. 왜 빠졌는지는 결과 파일의 universe 에 남는다.

    python market_tree.py                 # 최신 거래일 기준 수집
    python market_tree.py --date 2026-07-31
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime as dt

import market_flags

# 화면이 그릴 후보 수 (시장별). 실제로 몇 개를 그릴지는 화면이 폭에 맞춰 정한다.
TOP_N = 160

MARKETS = ("코스피", "코스닥")
MARKET_ARG = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

# 휴장일에 돌렸을 때 며칠까지 거슬러 올라가 볼지. 설·추석 연휴가 가장 길다.
BACKOFF_DAYS = 10

# 이 값 미만은 뺀다. 동전주는 한 호가에 몇 %씩 움직여 지도의 색을 다 먹는다.
EXCLUDE_UNDER = 1000

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
    ImportError 가 아닌 다른 예외가 튀어나오므로 넓게 잡는다.
    """
    try:
        from pykrx import stock
        return stock
    except Exception as e:                      # noqa: BLE001
        log(f"::warning::pykrx 를 쓸 수 없습니다 ({type(e).__name__}: {e})")
        return None


def traded(df) -> bool:
    """
    이 표에 '실제 거래'가 들어 있는가.

    휴장일에도 KRX 는 종목 목록을 돌려준다 — 코스피 943행, 코스닥 1820행이
    그대로 온다. 다만 종가가 전부 0 이다. "행이 있으면 거래일"로 보면 안 된다.
    """
    if df is None or df.empty or "종가" not in df:
        return False
    try:
        return bool((df["종가"] > 0).any())
    except Exception:                            # noqa: BLE001
        return False


def last_trading_day(stock, start: dt.date, back: int = BACKOFF_DAYS) -> str | None:
    """실제로 거래가 있었던 가장 가까운 과거 날짜."""
    for i in range(back):
        d = (start - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_sector_classifications(d, "KOSPI")
        except Exception as e:                   # noqa: BLE001
            log(f"  {d}: 조회 실패 ({type(e).__name__}) — 하루 앞으로")
            continue
        if traded(df):
            if i:
                log(f"  {start:%Y%m%d} 은 거래 없음 — {d} 로 물러섭니다")
            return d
        log(f"  {d}: 거래 없음 (휴장)")
    return None


def collect(date: str = None, exclude_under: int = EXCLUDE_UNDER) -> dict:
    """그날의 종목별 등락률·시가총액·업종. 제외 사유는 세어서 함께 돌려준다."""
    stock = _stock()
    if stock is None:
        raise SystemExit("pykrx 없이는 시장 지도를 만들 수 없습니다")

    if date is None:
        date = last_trading_day(stock, dt.date.today())
        if date is None:
            raise SystemExit(f"최근 {BACKOFF_DAYS}일 안에 거래가 있는 날이 "
                             "없습니다 — KRX 접속 문제로 보입니다")

    flags = market_flags.collect()
    admin, alert = flags["관리종목"], flags["투자주의환기종목"]

    items, cut = [], {"관리종목": 0, "투자주의환기종목": 0,
                      f"{exclude_under}원 미만": 0, "값 없음": 0}
    seen_total = 0

    for market in MARKETS:
        log(f"[수집] {market} {date}")
        try:
            df = stock.get_market_sector_classifications(date, MARKET_ARG[market])
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{market} 업종분류 실패 ({type(e).__name__}: {e})")
            continue
        if not traded(df):
            log(f"::warning::{market} {date} 는 거래가 없습니다")
            continue

        kept = 0
        for code, row in df.iterrows():
            seen_total += 1
            close = row.get("종가")
            chg = row.get("등락률")
            cap = row.get("시가총액")
            if not cap or cap <= 0 or close is None or chg is None:
                cut["값 없음"] += 1
                continue
            if code in admin:
                cut["관리종목"] += 1
                continue
            if code in alert:
                cut["투자주의환기종목"] += 1
                continue
            if close < exclude_under:
                cut[f"{exclude_under}원 미만"] += 1
                continue
            items.append({
                "code": code,
                "name": str(row.get("종목명") or code),
                "market": market,
                "sector": str(row.get("업종명") or "기타"),
                "chg": round(float(chg), 2),
                "cap": int(cap),
                "close": int(close),
            })
            kept += 1
        log(f"  {market} {len(df)}종목 중 {kept}종목 남김")

    if not items:
        raise SystemExit(f"{date} 시세를 받지 못했습니다")

    return {"date": date, "source": "pykrx", "items": items,
            "seen": seen_total, "cut": cut, "flags": flags["source"]}


# =============================================================================
# 집계
# =============================================================================

def sectors_of(items: list) -> list:
    """
    업종별 집계. 등락률은 **시가총액 가중 평균**이다.

    단순 평균을 내면 시총 1000억짜리 종목과 300조짜리가 같은 무게가 되어,
    작은 종목 몇 개가 업종 전체를 위아래로 끌고 간다.
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
    out = [{"name": s["name"], "cap": s["cap"], "n": s["n"],
            "up": s["up"], "down": s["down"],
            "chg": round(s["wsum"] / s["cap"], 2) if s["cap"] else 0.0}
           for s in agg.values()]
    out.sort(key=lambda s: -s["chg"])
    return out


def breadth_of(items: list) -> dict:
    return {"up": sum(1 for i in items if i["chg"] > 0),
            "down": sum(1 for i in items if i["chg"] < 0),
            "flat": sum(1 for i in items if i["chg"] == 0),
            "total": len(items)}


def build(raw: dict, top_n: int = TOP_N) -> dict:
    """
    화면이 읽을 모양으로. 집계는 **자르기 전 전 종목**으로 낸다.

    타일만 시장별 시총 상위로 자른다. 자른 뒤에 집계하면 '상승 2332' 가
    대형주만의 이야기가 된다.
    """
    items = raw["items"]
    by_market, tiles = {}, {}
    for m in MARKETS:
        rows = [i for i in items if i["market"] == m]
        by_market[m] = breadth_of(rows)
        top = sorted(rows, key=lambda i: -i["cap"])[:top_n]
        top.sort(key=lambda i: -i["chg"])
        tiles[m] = top
    return {
        "date": raw["date"],
        # 언제 받은 것인지 **시각까지** 남긴다.
        #
        #   날짜만 있으면 장중에 15분마다 다시 받아도 값이 "20260804" 로
        #   똑같아서, 화면이 새것인지 옛것인지 가릴 수가 없다. 실제로 화면은
        #   날짜만 보고 '같은 것' 이라 판단해 새로 받은 지도를 계속 버렸다.
        "fetched_at": dt.datetime.now(dt.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": raw["source"],
        "universe": {"seen": raw["seen"], "kept": len(items),
                     "cut": raw["cut"], "flags": raw["flags"]},
        "breadth": breadth_of(items),
        "markets": {m: {"breadth": by_market[m], "items": tiles[m]}
                    for m in MARKETS},
        "sectors": sectors_of(items),
    }


def save(payload: dict, full: dict = None) -> None:
    """
    docs/ 에는 화면이 쓸 것만, store/ 에는 전 종목을 그대로 남긴다.

    나중에 "이 조건이면 몇 종목이냐" 같은 질문에 답하려면 자르기 전 목록이
    있어야 한다. docs/ 에 전부 넣으면 폰이 매번 그걸 다 내려받는다.
    """
    os.makedirs(os.path.dirname(DOCS_PATH) or ".", exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(full or payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (전 종목), {DOCS_PATH} (화면용)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="시장 지도 (트리맵) 데이터 수집")
    p.add_argument("--date", help="YYYY-MM-DD 또는 YYYYMMDD (기본: 최근 거래일)")
    p.add_argument("--top", type=int, default=TOP_N, help=f"시장별 후보 수 (기본 {TOP_N})")
    p.add_argument("--under", type=int, default=EXCLUDE_UNDER,
                   help=f"이 값 미만 제외 (기본 {EXCLUDE_UNDER}원, 0 이면 제외 안 함)")
    a = p.parse_args(argv)

    raw = collect(a.date.replace("-", "") if a.date else None, a.under)
    payload = build(raw, a.top)
    save(payload, {**payload, "items": raw["items"]})

    u = payload["universe"]
    log(f"\n{payload['date']} · 조회 {u['seen']}종목 → 남은 {u['kept']}종목")
    for k, v in u["cut"].items():
        if v:
            log(f"  제외 {k}: {v}종목")
    for m in MARKETS:
        b = payload["markets"][m]["breadth"]
        log(f"  {m}: {b['total']}종목 (↑{b['up']} ↓{b['down']} －{b['flat']}) "
            f"· 타일 후보 {len(payload['markets'][m]['items'])}")
    log(f"업종 {len(payload['sectors'])}개")
    log("  상위 5: " + ", ".join(f"{s['name']} {s['chg']:+.2f}%"
                                for s in payload["sectors"][:5]))
    log("  하위 5: " + ", ".join(f"{s['name']} {s['chg']:+.2f}%"
                                for s in payload["sectors"][-5:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
