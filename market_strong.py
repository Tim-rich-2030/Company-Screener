# -*- coding: utf-8 -*-
"""
지수보다 강한 종목 — 20일선 이격을 지수와 견준다.

지수가 20일선 아래 7% 인 날, 어떤 종목은 20일선 위 5% 에 있다. 그 차이가
"오늘 시장보다 강했다"는 뜻이다. 등락률 한 줄로는 안 보인다 — 어제 20% 오른
종목이 오늘 1% 오른 것과, 석 달 눌려 있던 종목이 오늘 1% 오른 것은 다르다.

    이격도 = (종가 - 20일 이동평균) / 20일 이동평균 × 100
    강함   = 종목 이격도 - 그 시장 지수의 이격도

**착시를 그대로 두지 않는다.** 테마에 엮인 종목은 며칠 만에 이격이 벌어졌다가
그만큼 빠진다. 그래서 테마에 걸린 종목은 어느 테마인지 오른쪽에 적는다.
숨기지도 빼지도 않는다 — 왜 강한지 보고 판단할 몫은 보는 사람 것이다.

받아오는 방법:

    전종목 일별 시세는 **하루에 한 번**이면 다 온다
    (get_market_ohlcv_by_ticker). 20일선을 만들려고 20번 두드린다.
    종목마다 20일씩 따로 받으면 2,500종목 × 20일이 된다.

    상세 화면이 쓰는 것들도 전부 전종목 한 번짜리다.
      · PBR·PER·배당수익률   get_market_fundamental_by_ticker
      · 외국인소진율          get_exhaustion_rates_of_foreign_investment_by_ticker
      · 개인·외국인 순매수    get_market_net_purchases_of_equities

    python market_strong.py
    python market_strong.py --date 20260803 --days 20
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime as dt

import market_tree
import market_flags

DAYS = 20                # 이동평균 길이
BACK_CAL = 45            # 영업일 20일을 채우려고 달력으로 거슬러 갈 최대 일수
TOP = 40                 # 목록 하나에 담을 종목 수
EXCLUDE_UNDER = market_tree.EXCLUDE_UNDER

MARKETS = ("코스피", "코스닥")
MARKET_ARG = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

SIGNAL_PATH = os.path.join("store", "market_signal.json")
THEME_PATH = os.path.join("store", "market_theme.json")
STORE_PATH = os.path.join("store", "market_strong.json")
DOCS_PATH = os.path.join("docs", "market_strong.json")


def log(msg: str) -> None:
    print(msg, flush=True)


def _f(row, key):
    """표의 칸 하나를 실수로. 없거나 못 읽으면 None — 0 으로 바꾸지 않는다."""
    try:
        v = row.get(key) if hasattr(row, "get") else row[key]
    except Exception:                            # noqa: BLE001
        return None
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v                 # NaN 거르기


# =============================================================================
# 받아오기
# =============================================================================

def fetch_history(stock, end: dt.date, days: int = DAYS):
    """
    최근 영업일 `days` 개의 전종목 시세. 오래된 날이 앞에 온다.

    휴장일에도 KRX 는 행을 돌려주므로 종가가 하나라도 0 보다 큰지로 거른다
    (market_tree.traded 와 같은 판정이다).
    """
    out, tried = [], 0
    d = end
    while len(out) < days and tried < BACK_CAL:
        ymd = d.strftime("%Y%m%d")
        tried += 1
        d -= dt.timedelta(days=1)
        try:
            df = stock.get_market_ohlcv_by_ticker(ymd, "ALL")
        except Exception as e:                   # noqa: BLE001
            log(f"  {ymd}: 조회 실패 ({type(e).__name__}: {e})")
            continue
        if not market_tree.traded(df):
            continue
        out.append((ymd, df))
    out.reverse()
    return out


def series_of(hist: list, days: int = DAYS):
    """
    {종목코드: [종가 …]} — 오래된 것부터. 20일을 다 채운 종목만 남긴다.

    중간에 거래정지된 날이 있으면 그 종목은 20개가 안 채워진다. 모자란 채로
    평균을 내면 '19일 평균'을 20일선이라고 부르게 되므로 아예 뺀다.
    """
    closes, today = {}, {}
    for i, (_, df) in enumerate(hist):
        last = (i == len(hist) - 1)
        for code, row in df.iterrows():
            c = _f(row, "종가")
            if not c or c <= 0:
                continue
            closes.setdefault(code, []).append(c)
            if last:
                today[code] = {
                    "close": int(c),
                    "open": int(_f(row, "시가") or 0),
                    "high": int(_f(row, "고가") or 0),
                    "low": int(_f(row, "저가") or 0),
                    "chg": round(_f(row, "등락률") or 0.0, 2),
                    "volume": int(_f(row, "거래량") or 0),
                    "value": int(_f(row, "거래대금") or 0),
                }
    full = {c: v for c, v in closes.items() if len(v) == days}
    return full, today


def fetch_facts(stock, date: str, codes: set) -> dict:
    """
    상세 화면이 쓰는 것들. 전부 전종목 한 번짜리라 종목 수와 상관없이 값이 싸다.

    하나가 실패해도 나머지는 채운다. 못 받은 칸은 화면에서 '–' 로 남는다 —
    0 으로 채우면 배당이 없는 회사와 못 받은 회사를 구분할 수 없다.
    """
    facts, got = {}, {}

    def put(code, key, val):
        if code in codes and val is not None:
            facts.setdefault(code, {})[key] = val

    for market in MARKETS:
        arg = MARKET_ARG[market]
        try:
            df = stock.get_market_fundamental_by_ticker(date, arg)
            for code, row in df.iterrows():
                put(code, "per", _f(row, "PER"))
                put(code, "pbr", _f(row, "PBR"))
                put(code, "div", _f(row, "DIV"))
                put(code, "eps", _f(row, "EPS"))
                put(code, "bps", _f(row, "BPS"))
            got["투자지표"] = got.get("투자지표", 0) + len(df)
        except Exception as e:                   # noqa: BLE001
            log(f"  {market} 투자지표 실패 ({type(e).__name__}: {e})")

        try:
            df = stock.get_exhaustion_rates_of_foreign_investment_by_ticker(date, arg)
            for code, row in df.iterrows():
                put(code, "foreign", _f(row, "지분율"))
            got["외국인"] = got.get("외국인", 0) + len(df)
        except Exception as e:                   # noqa: BLE001
            log(f"  {market} 외국인소진율 실패 ({type(e).__name__}: {e})")

        for who, key in (("개인", "buy_person"), ("외국인", "buy_foreign")):
            try:
                df = stock.get_market_net_purchases_of_equities(
                    date, date, arg, who)
                for code, row in df.iterrows():
                    put(code, key, _f(row, "순매수거래대금"))
                got[f"{who} 순매수"] = got.get(f"{who} 순매수", 0) + len(df)
            except Exception as e:               # noqa: BLE001
                log(f"  {market} {who} 순매수 실패 ({type(e).__name__}: {e})")

    log("  상세 자료: " + (", ".join(f"{k} {v}행" for k, v in got.items())
                           or "하나도 못 받음"))
    return facts


# =============================================================================
# 테마 · 지수
# =============================================================================

def theme_index(path: str = THEME_PATH) -> dict:
    """
    {종목코드: [{대분류, 소분류}, …]}

    테마 수집이 실패한 날은 빈 표를 돌려준다. 뱃지가 안 붙을 뿐, 목록은 나온다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        log("  테마 자료가 없습니다 — 테마 표시 없이 만듭니다")
        return {}
    out = {}
    for g in data.get("groups") or []:
        for s in g.get("subs") or []:
            for code in s.get("codes") or []:
                tags = out.setdefault(code, [])
                if len(tags) < 2:                # 뱃지는 두 개까지
                    tags.append({"group": g["name"], "sub": s["name"]})
    if out:
        log(f"  테마에 엮인 종목 {len(out)}개")
    return out


def index_disparity(path: str = SIGNAL_PATH) -> dict:
    """
    {시장: 지수 이격도}. 화면 첫 장에 쓰는 값을 그대로 가져온다.

    여기서 따로 계산하면 같은 화면에 '코스피 -7.00%' 와 '지수 -6.93%' 가 같이
    나올 수 있다. 기준은 하나여야 한다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    idx = ((data.get("computed") or data).get("indices")) or {}
    out = {}
    for name, d in idx.items():
        v = d.get("disparity")
        if v is not None:
            out[name] = float(v)
    return out


# =============================================================================
# 만들기
# =============================================================================

def build(rows: list, index_disp: dict, top: int = TOP) -> dict:
    """
    시장별 '지수보다 강한 종목' 과 시장 전체의 급상승·급하락.

    강한 종목 정렬은 **1차 이격도, 2차 거래대금**이다. 이격이 같으면 거래가
    실린 쪽을 앞에 둔다 — 같은 이격이라도 손이 많이 탄 쪽이 시장이 본 종목이다.
    """
    markets = {}
    for m in MARKETS:
        mine = [r for r in rows if r["market"] == m]
        base = index_disp.get(m)
        strong = []
        if base is not None:
            strong = sorted([r for r in mine if r["disparity"] > base],
                            key=lambda r: (-r["disparity"], -r["value"]))
        markets[m] = {
            "index_disparity": None if base is None else round(base, 2),
            "counted": len(mine),
            "strong_total": len(strong),
            "strong": strong[:top],
        }
    up = sorted(rows, key=lambda r: -r["chg"])[:top]
    down = sorted(rows, key=lambda r: r["chg"])[:top]
    return {"markets": markets, "급상승": up, "급하락": down}


def collect(date: str = None, days: int = DAYS, top: int = TOP) -> dict:
    stock = market_tree._stock()
    if stock is None:
        raise SystemExit("pykrx 없이는 만들 수 없습니다")

    end = dt.date.today() if date is None else dt.datetime.strptime(
        date, "%Y%m%d").date()
    hist = fetch_history(stock, end, days)
    if len(hist) < days:
        raise SystemExit(f"영업일 {days}일치를 못 채웠습니다 ({len(hist)}일) — "
                         "KRX 접속 문제로 보입니다")
    date = hist[-1][0]
    log(f"[수집] {hist[0][0]} ~ {date} · 영업일 {len(hist)}일")

    closes, today = series_of(hist, days)
    log(f"  {days}일을 다 채운 종목 {len(closes)}개")

    flags = market_flags.collect()
    admin, alert = flags["관리종목"], flags["투자주의환기종목"]
    themes = theme_index()

    rows, cut = [], {"관리종목": 0, "투자주의환기종목": 0,
                     f"{EXCLUDE_UNDER}원 미만": 0, f"{days}일 미만": 0}
    seen = 0
    for market in MARKETS:
        try:
            df = stock.get_market_sector_classifications(date, MARKET_ARG[market])
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{market} 업종분류 실패 ({type(e).__name__}: {e})")
            continue
        for code, row in df.iterrows():
            seen += 1
            t = today.get(code)
            if code not in closes or not t:
                cut[f"{days}일 미만"] += 1
                continue
            if code in admin:
                cut["관리종목"] += 1
                continue
            if code in alert:
                cut["투자주의환기종목"] += 1
                continue
            if t["close"] < EXCLUDE_UNDER:
                cut[f"{EXCLUDE_UNDER}원 미만"] += 1
                continue
            vals = closes[code]
            sma = sum(vals) / len(vals)
            if sma <= 0:
                continue
            rows.append({
                "code": code,
                "name": str(row.get("종목명") or code),
                "market": market,
                "sector": str(row.get("업종명") or "기타"),
                "cap": int(_f(row, "시가총액") or 0),
                "sma20": round(sma, 1),
                "disparity": round((t["close"] - sma) / sma * 100, 2),
                "themes": themes.get(code, []),
                **t,
            })

    if not rows:
        raise SystemExit(f"{date} 계산할 종목이 없습니다")
    log(f"  전체 {seen}종목 중 {len(rows)}종목 계산 "
        + " · ".join(f"{k} {v}" for k, v in cut.items() if v))

    index_disp = index_disparity()
    if not index_disp:
        log("::warning::지수 이격도를 못 읽었습니다 — '지수보다 강한' 목록이 빕니다")
    out = build(rows, index_disp, top)

    shown = {r["code"] for lst in
             [out["급상승"], out["급하락"]] + [m["strong"] for m in out["markets"].values()]
             for r in lst}
    facts = fetch_facts(stock, date, shown)

    # 상세 화면이 그릴 20일 종가. 보여줄 종목만 담는다 (2,500종목을 다 담으면
    # 화면이 읽어야 할 파일이 몇 MB 가 된다).
    series = {c: [int(v) for v in closes[c]] for c in shown if c in closes}

    return {
        "date": date, "source": "pykrx", "days": days,
        "from": hist[0][0],
        "seen": seen, "counted": len(rows), "cut": cut,
        "markets": out["markets"], "급상승": out["급상승"], "급하락": out["급하락"],
        "facts": facts, "series": series,
    }


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="지수보다 강한 종목")
    p.add_argument("--date", help="기준일 YYYYMMDD (없으면 최근 영업일)")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--top", type=int, default=TOP)
    a = p.parse_args(argv)

    payload = collect(a.date, a.days, a.top)
    save(payload)

    log(f"\n{payload['date']} · {payload['days']}일선 기준")
    for m, d in payload["markets"].items():
        base = d["index_disparity"]
        log(f"  [{m}] 지수 이격도 {'–' if base is None else f'{base:+.2f}%'} · "
            f"강한 종목 {d['strong_total']}/{d['counted']}")
        for r in d["strong"][:5]:
            tag = " ".join(t["sub"] for t in r["themes"]) or "-"
            log(f"      {r['name'][:12]:12} 이격 {r['disparity']:+6.2f}%  "
                f"{r['chg']:+6.2f}%  {r['value']/1e8:8,.0f}억  {tag}")
    log("  급상승 5: " + ", ".join(f"{r['name']} {r['chg']:+.1f}%"
                                   for r in payload["급상승"][:5]))
    log("  급하락 5: " + ", ".join(f"{r['name']} {r['chg']:+.1f}%"
                                   for r in payload["급하락"][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
