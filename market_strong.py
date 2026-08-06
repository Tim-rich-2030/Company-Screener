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
import time
import argparse
import datetime as dt

import market_tree
import market_flags

DAYS = 20                # 이동평균 길이
BACK_CAL = 45            # 영업일 20일을 채우려고 달력으로 거슬러 갈 최대 일수
TOP = 25                 # 목록 하나에 담을 종목 수
EXCLUDE_UNDER = market_tree.EXCLUDE_UNDER

# 거래가 거의 없는 종목을 뺀다.
#
#   처음엔 안 걸렀다. 그랬더니 코스닥 1~6위가 거래대금 1억·4,438만·3,105만원
#   짜리로 채워졌다. 못 사고 못 파는 종목이다. 게다가 20일 사이 몇 배가 된
#   종목이라 이격도가 +263% 로 나오는데, 그쯤 되면 '20일선 대비'라는 말의 뜻이
#   사라진다. 코스피는 상위 40개 중 거래대금 100억 이상이 둘뿐이었다.
#
#   금액을 고정으로 박아 두면 한산한 날엔 목록이 확 줄고 터진 날엔 헐렁해진다.
#   그래서 그날 몇 종목이 이 문턱을 넘었는지 화면과 저장 파일에 함께 적는다.
#   1,000억으로 잡아 봤더니 코스피 26 · 코스닥 8종목만 남았다. 목록 25칸을
#   채우지 못한다. 그날 분포는 이랬다 (100/300/500/1,000억):
#     코스피 146 · 85 · 58 · 26      코스닥 91 · 40 · 25 · 8
#   300억이면 코스피 85 · 코스닥 40 이라 25칸이 채워지고, 그 아래는 하루
#   몇십억짜리라 여전히 걸러진다.
MIN_VALUE = 30_000_000_000      # 거래대금 300억
MIN_CAP = 100_000_000_000       # 시가총액 1,000억

# 문턱을 다시 정할 때 쓰려고 남기는 분포 (단위: 억). 상위 몇 개만 저장하면
# 정작 문턱을 옮길 근거가 없다 — 실제로 그래서 한 번 더 돌려야 했다.
LIQ_STEPS = (10, 50, 100, 300, 500, 1000, 3000)

# 상세 화면 차트용 일봉. 120일선이 화면에 남으려면 240봉쯤은 있어야 한다.
PX_DAYS = 250
PX_PAUSE = 0.05          # 종목마다 한 번씩 부르는 유일한 자리다

MARKETS = ("코스피", "코스닥")
MARKET_ARG = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}

SIGNAL_PATH = os.path.join("store", "market_signal.json")
THEME_PATH = os.path.join("store", "market_theme.json")
STORE_PATH = os.path.join("store", "market_strong.json")
DOCS_PATH = os.path.join("docs", "market_strong.json")
# 일봉은 따로 둔다. 첫 화면이 읽을 이유가 없고, 같이 넣으면 목록을 보려고
# 1MB 를 먼저 받게 된다. 종목을 눌렀을 때만 받는다.
PX_PATH = os.path.join("docs", "market_px.json")
# 장중에 다시 셀 때 쓰는 밑감. 20일치를 또 받지 않으려고 남긴다.
BASE_PATH = os.path.join("store", "strong_base.json")


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


def fetch_px(stock, codes, date: str, days: int = PX_DAYS) -> dict:
    """
    목록에 나온 종목의 **일봉**. {코드: [[날짜, 시,고,저,종], …]}

    상세 화면의 차트를 지수 차트와 같은 모양(봉 + 20·60·120일선)으로 그리려면
    120일선이 화면에 남을 만큼은 있어야 한다. 그래서 1년치를 받는다.
    주봉·월봉은 따로 받지 않고 화면에서 일봉을 묶어 만든다.

    여기만 **종목마다 한 번씩** 부른다. 전종목 한 번짜리로는 과거를 못 받는다.
    대신 목록에 실제로 나온 종목(최대 160개)만 받는다.
    """
    end = dt.datetime.strptime(date, "%Y%m%d").date()
    start = (end - dt.timedelta(days=int(days * 1.55))).strftime("%Y%m%d")
    out, failed = {}, []
    for code in sorted(codes):
        try:
            df = stock.get_market_ohlcv_by_date(start, date, code)
        except Exception as e:                   # noqa: BLE001
            failed.append(code)
            if len(failed) <= 3:
                log(f"  일봉 실패 {code} ({type(e).__name__}: {e})")
            continue
        rows = []
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                c = _f(row, "종가")
                if not c or c <= 0:              # 거래정지일은 0 으로 온다
                    continue
                rows.append([idx.strftime("%Y%m%d"), int(_f(row, "시가") or c),
                             int(_f(row, "고가") or c), int(_f(row, "저가") or c),
                             int(c)])
        if len(rows) >= 2:
            out[code] = rows[-days:]
        else:
            failed.append(code)
        time.sleep(PX_PAUSE)
    log(f"  일봉 {len(out)}종목 (실패 {len(failed)}) · "
        f"평균 {sum(len(v) for v in out.values()) // max(1, len(out))}일")
    return out


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

def liquid(r, min_value: int = MIN_VALUE, min_cap: int = MIN_CAP) -> bool:
    return r["value"] >= min_value and r["cap"] >= min_cap


def spread(rows: list) -> dict:
    """시장별 거래대금 분포. 문턱을 다시 정할 때 이것만 보면 된다."""
    out = {}
    for m in MARKETS:
        vals = [r["value"] for r in rows if r["market"] == m]
        out[m] = {f"{s}억 이상": sum(1 for v in vals if v >= s * 1e8)
                  for s in LIQ_STEPS}
    return out


def build(rows: list, index_disp: dict, top: int = TOP,
          min_value: int = MIN_VALUE, min_cap: int = MIN_CAP) -> dict:
    """
    시장별 '지수보다 강한 종목' 과 시장 전체의 급상승·급하락.

    **네 목록 모두 거래대금·시가총액 문턱을 넘은 종목만 담는다.** 못 사고 못
    파는 종목을 목록에 올리면 그 줄은 읽을 이유가 없다.

    강한 종목 **정렬은 등락률순**이다. 목록에 드는 조건은 여전히 이격도지만
    (지수보다 위), 줄을 세우는 자는 오늘 얼마나 움직였느냐다. 이격도는
    '20일 평균에서 얼마나 떨어져 있나'라 며칠째 같은 종목이 위에 남는다.
    같은 등락률이면 이격이 큰 쪽을 앞에 둔다.

    '강하다'의 기준은 좁히지 않았다. 지수 이격도보다 위면 강한 것이 맞고,
    대신 몇 종목 중 몇 종목인지를 그대로 적어 화면이 상황을 말하게 한다.
    """
    ok = [r for r in rows if liquid(r, min_value, min_cap)]
    markets = {}
    for m in MARKETS:
        mine = [r for r in ok if r["market"] == m]
        base = index_disp.get(m)
        strong = []
        if base is not None:
            strong = sorted([r for r in mine if r["disparity"] > base],
                            key=lambda r: (-r["chg"], -r["disparity"]))
        markets[m] = {
            "index_disparity": None if base is None else round(base, 2),
            "counted": sum(1 for r in rows if r["market"] == m),
            "liquid": len(mine),
            "strong_total": len(strong),
            "strong": strong[:top],
        }
    up = sorted(ok, key=lambda r: -r["chg"])[:top]
    down = sorted(ok, key=lambda r: r["chg"])[:top]
    return {"markets": markets, "급상승": up, "급하락": down,
            "floor": {"거래대금": min_value, "시가총액": min_cap},
            "liquid_total": len(ok), "spread": spread(rows)}


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
    base, meta = {}, {}
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
            # 장중에 20일선을 다시 낼 때 쓸 밑감.
            #   [스무 날 합, 가장 오래된 날, 가장 최근 날]
            # 새 날이면  (합 - 가장 오래된 날 + 지금값) / 20
            # 같은 날이면 (합 - 가장 최근 날 + 지금값) / 20
            # 어느 쪽이든 **정확히 스무 날**이다. 어제 평균에 오늘 값을 얹는
            # 어림이 아니다.
            base[code] = [round(sum(vals), 1), vals[0], vals[-1]]
            meta[code] = {"name": str(row.get("종목명") or code),
                          "market": market,
                          "sector": str(row.get("업종명") or "기타"),
                          "cap": int(_f(row, "시가총액") or 0),
                          "themes": themes.get(code, [])}
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

    px = fetch_px(stock, shown, date)

    save_base({"date": date, "days": days, "base": base, "meta": meta})

    return {
        "px": px,
        "date": date, "source": "pykrx", "days": days,
        # **언제 만든 것인지 시각까지 남긴다.**
        #
        #   날짜만 있으면 같은 날 만든 두 판(장중 15:40 것과 마감 뒤 17:30 것)
        #   중 어느 쪽이 나중인지 가릴 수가 없다. live 가지에 올릴 때 그걸
        #   가려야 한다 — 옛것이 새것을 덮으면 어제 등락이 오늘로 보인다.
        "fetched_at": dt.datetime.now(dt.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": hist[0][0],
        "seen": seen, "counted": len(rows), "cut": cut,
        "floor": out["floor"], "liquid_total": out["liquid_total"],
        "spread": out["spread"],
        "markets": out["markets"], "급상승": out["급상승"], "급하락": out["급하락"],
        "facts": facts,
    }


def save_base(payload: dict) -> None:
    """장중에 다시 셀 때 쓰는 밑감. store/ 에만 둔다 — 화면이 읽을 것이 아니다."""
    os.makedirs(os.path.dirname(BASE_PATH) or ".", exist_ok=True)
    with open(BASE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    kb = os.path.getsize(BASE_PATH) / 1024
    log(f"[저장] {BASE_PATH} ({len(payload['base'])}종목, {kb:,.0f}KB)")


def live_index(path: str = os.path.join("docs", "market_board.json")) -> dict:
    """
    지금 지수 이격도. 현황판이 방금 받은 코스피·코스닥 실시간 값으로 낸다.

    장중에 종목만 지금 값으로 재고 지수는 어제 것으로 두면, '지수보다 강한'
    이 통째로 기울어진다 — 시장이 3% 빠진 날 모든 종목이 강해 보인다.
    비교할 두 값은 같은 시각의 것이어야 한다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            board = json.load(f)
        with open(SIGNAL_PATH, encoding="utf-8") as f:
            sig = json.load(f)
    except (OSError, ValueError):
        return {}
    now = {}
    for r in board.get("night") or []:
        if r.get("market") == "KR" and r.get("last"):
            now[r["name"]] = float(r["last"])
    out = {}
    for name, series in (sig.get("series") or {}).items():
        p = now.get(name)
        if p is None:
            continue
        days = sorted(series)[-DAYS:]
        if len(days) < DAYS:
            continue
        closes = []
        for d in days:
            v = series[d]
            closes.append(float(v[3] if isinstance(v, list) and len(v) >= 4 else v))
        # 오늘이 이미 그 안에 있으면 마지막 날을, 아니면 가장 오래된 날을 뺀다.
        drop = closes[-1] if days[-1] == max(days) and len(days) == DAYS else closes[0]
        sma = (sum(closes) - drop + p) / DAYS
        if sma > 0:
            out[name] = round((p - sma) / sma * 100, 2)
    return out


def quick(top: int = TOP) -> dict:
    """
    장중 다시 세기 — **오늘 시세 한 번만 받는다.**

    하루 한 번 도는 판이 20일치를 받아 두고 종목마다 [스무 날 합, 첫날, 끝날]
    을 남겼다. 여기서는 오늘 전종목 시세 한 번(호출 1회)만 받아 그 합을 굴린다.
    20일치를 다시 받으면 호출이 스무 번이고, 15분마다 그러면 하루 열두 시간
    장중에 KRX 를 천 번 두드리게 된다.

    **어제 목록에 없던 종목도 들어온다** — 밑감에 그날 20일을 채운 종목이
    전부(2,800여 개) 들어 있기 때문이다. 오늘 급등해 문턱을 넘으면 잡힌다.

    투자지표·순매수·일봉은 지난 판 것을 그대로 쓴다. 그건 하루 단위 값이라
    장중에 바뀌지 않는다.
    """
    stock = market_tree._stock()
    if stock is None:
        raise SystemExit("pykrx 없이는 만들 수 없습니다")
    try:
        with open(BASE_PATH, encoding="utf-8") as f:
            b = json.load(f)
        with open(STORE_PATH, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"밑감을 못 읽었습니다 ({type(e).__name__}) — "
                         "하루 한 번 도는 판이 먼저 돌아야 합니다")

    base, meta = b.get("base") or {}, b.get("meta") or {}
    hist = fetch_history(stock, dt.date.today(), days=1)
    if not hist:
        raise SystemExit("오늘 시세를 못 받았습니다")
    date, df = hist[-1]
    same_day = (date == b.get("date"))
    log(f"[장중] {date} · 밑감 {b.get('date')} ({'같은 날' if same_day else '새 날'})"
        f" · 종목 {len(base)}")

    rows = []
    for code, row in df.iterrows():
        m, bb = meta.get(code), base.get(code)
        if not m or not bb:
            continue
        c = _f(row, "종가")
        if not c or c <= 0 or c < EXCLUDE_UNDER:
            continue
        total, oldest, newest = bb
        sma = (total - (newest if same_day else oldest) + c) / DAYS
        if sma <= 0:
            continue
        rows.append({
            "code": code, "name": m["name"], "market": m["market"],
            "sector": m["sector"], "cap": m["cap"], "themes": m["themes"],
            "sma20": round(sma, 1),
            "disparity": round((c - sma) / sma * 100, 2),
            "close": int(c), "open": int(_f(row, "시가") or 0),
            "high": int(_f(row, "고가") or 0), "low": int(_f(row, "저가") or 0),
            "chg": round(_f(row, "등락률") or 0.0, 2),
            "volume": int(_f(row, "거래량") or 0),
            "value": int(_f(row, "거래대금") or 0),
        })
    if not rows:
        raise SystemExit("계산할 종목이 없습니다")

    disp = live_index() or index_disparity()
    out = build(rows, disp, top)
    log(f"  {len(rows)}종목 · 지수 이격도 {disp}")
    for m, d in out["markets"].items():
        log(f"  [{m}] 문턱 통과 {d['liquid']} · 강한 종목 {d['strong_total']}")

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # fetched_at 을 **반드시 다시 찍는다.** {**prev} 로 지난 판을 펼치므로
    # 그냥 두면 하루 한 번 판이 찍어 둔 어제 시각을 그대로 물려받는다.
    # 화면도 live 도 fetched_at 을 먼저 보기 때문에, 그러면 방금 만든 것이
    # 어제 것으로 취급돼 장중 갱신이 통째로 막힌다.
    return {**prev, "date": date, "source": "pykrx (장중)",
            "counted": len(rows), "floor": out["floor"],
            "liquid_total": out["liquid_total"], "spread": out["spread"],
            "markets": out["markets"],
            "급상승": out["급상승"], "급하락": out["급하락"],
            "fetched_at": now, "intraday_at": now}


def save(payload: dict) -> None:
    px = payload.pop("px", None)
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    # 일봉은 **받았을 때만** 쓴다.
    #
    #   장중 판(--quick)에는 일봉이 없다. 없는 것을 빈 채로 쓰면 673KB 짜리
    #   docs/market_px.json 이 통째로 지워지고, 종목을 눌러도 차트가 안 나온다.
    #   빠진 것과 빈 것은 다르다.
    if px is None:
        log(f"[저장] {STORE_PATH}, {DOCS_PATH} (일봉은 그대로 둡니다)")
        return
    # 일봉은 docs/ 에만. store/ 에도 두면 저장소가 하루에 두 배로 커진다.
    os.makedirs(os.path.dirname(PX_PATH) or ".", exist_ok=True)
    with open(PX_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": payload["date"], "days": PX_DAYS, "px": px},
                  f, ensure_ascii=False, separators=(",", ":"))
    kb = os.path.getsize(PX_PATH) / 1024
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}, {PX_PATH} ({kb:,.0f}KB)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="지수보다 강한 종목")
    p.add_argument("--date", help="기준일 YYYYMMDD (없으면 최근 영업일)")
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--top", type=int, default=TOP)
    p.add_argument("--quick", action="store_true",
                   help="장중 다시 세기 — 오늘 시세 한 번만 받는다")
    a = p.parse_args(argv)

    if a.quick:
        payload = quick(a.top)
        save(payload)
        for m, d in payload["markets"].items():
            log(f"  [{m}] 강한 종목 {d['strong_total']} · "
                + ", ".join(f"{r['name']} {r['disparity']:+.1f}%"
                            for r in d["strong"][:3]))
        return 0

    payload = collect(a.date, a.days, a.top)
    save(payload)

    f = payload["floor"]
    log(f"\n{payload['date']} · {payload['days']}일선 기준 · "
        f"거래대금 {f['거래대금']/1e8:,.0f}억 · 시총 {f['시가총액']/1e8:,.0f}억 이상")
    for m, sp in payload["spread"].items():
        log(f"  [{m}] 거래대금 분포 " + " · ".join(f"{k} {v}" for k, v in sp.items()))
    for m, d in payload["markets"].items():
        base = d["index_disparity"]
        log(f"  [{m}] 지수 이격도 {'–' if base is None else f'{base:+.2f}%'} · "
            f"문턱 통과 {d['liquid']}/{d['counted']} · "
            f"그중 강한 종목 {d['strong_total']}")
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
