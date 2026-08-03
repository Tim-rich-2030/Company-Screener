# -*- coding: utf-8 -*-
"""
거시 지표 — 무엇이 언제 어떻게 움직였고, 그 다음에 무슨 일이 있었나.

뉴스 목록 대신 **기록**을 둔다. "FOMC 가 7월 29일에 0.25%p 내렸다" 는 사실이고,
"그 전에도 아홉 번 내렸는데 그 뒤 20거래일 코스피가 여섯 번 올랐다" 도 사실이다.
둘을 나란히 두면 판단은 보는 사람이 한다. 앱은 판단하지 않는다.

**틀**
  지표(SERIES) 하나마다
    · 값의 흐름     — 차트로 그릴 점들
    · 사건(events)  — 값이 바뀐 날. 정책금리는 바뀐 날이 곧 결정일이다
    · 이후 기록      — 사건 뒤 20·60거래일 코스피가 어땠는지, 표본 몇 개로

지표를 늘리려면 SERIES 에 한 줄 더하면 된다. 금리로 시작하지만 환율·유가도
같은 틀이다.

**사건을 따로 받아오지 않는 이유**: 금리는 값이 바뀐 날이 곧 인상·인하일이다.
회의 일정표를 따로 긁어 맞추는 것보다 값에서 뽑는 편이 틀릴 여지가 없다.

    python market_macro.py              # 수집·저장
    python market_macro.py --dump       # 받아온 원본 확인
"""
from __future__ import annotations

import os
import io
import re
import sys
import csv
import json
import argparse
import datetime as dt

import requests

STORE_PATH = os.path.join("store", "market_macro.json")
DOCS_PATH = os.path.join("docs", "market_macro.json")

YEARS = 10               # 표본을 늘리려면 길어야 한다. 3년이면 결정이 스무 번뿐이다
AFTER = (20, 60)         # 사건 뒤 며칠을 볼지 (거래일)
CHART_POINTS = 400       # 화면에 보낼 점 수. 그보다 촘촘해도 폰에서 안 보인다
TIMEOUT = 30
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
# ECOS 는 무료 키가 필요하다. 없으면 한국 기준금리만 빠지고 나머지는 그대로 나온다.
ECOS_URL = ("https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000"
            "/{stat}/{cycle}/{start}/{end}/{item}")

# 지표 정의. step=True 면 '값이 바뀐 날'을 사건으로 본다 (정책금리처럼 계단 모양).
SERIES = [
    {"key": "us_rate", "name": "미국 기준금리", "unit": "%", "step": True,
     "fred": "DFEDTARU", "note": "연방기금목표금리 상단"},
    # ECOS 키가 있으면 한국은행 기준금리를 그대로 받는다. 키가 없거나 ECOS 가
    # 막혀 있으면 FRED 의 한국 단기금리로 대신하되, **이름과 설명을 바꿔 단다**.
    # 콜금리는 기준금리를 따라다니지만 같은 값이 아니다. 같은 이름을 달면
    # 대용을 원본으로 읽게 된다.
    {"key": "kr_rate", "name": "한국 기준금리", "unit": "%", "step": True,
     "ecos": {"stat": "722Y001", "cycle": "D", "item": "0101000"},
     "note": "한국은행 기준금리",
     # 대용은 **계단이 아니다.** 콜금리는 시장에서 매일 조금씩 움직이는 값이라,
     # 기준금리처럼 '값이 바뀐 날 = 결정일'로 보면 2.541 → 2.527 같은 미세 변동이
     # 전부 '인하'로 찍힌다. 실제로 그렇게 24건이 나왔다. 대용일 때는 선으로만
     # 그리고 사건을 잡지 않는다.
     "fallback": {"name": "한국 단기금리", "step": False,
                  "note": "기준금리 대용 — ECOS 키가 없어 FRED 의 한국 "
                          "콜/단기금리로 대신함. 시장금리라 매일 조금씩 움직인다",
                  "fred": ["IRSTCI01KRM156N", "IR3TIB01KRM156N",
                           "INTDSRKRM193N"]}},
    {"key": "usdkrw", "name": "원/달러", "unit": "원", "step": False,
     "fred": "DEXKOUS", "note": "매매기준율(FRED)"},
    {"key": "wti", "name": "WTI 유가", "unit": "달러", "step": False,
     "fred": "DCOILWTICO", "note": "서부텍사스산 현물"},
    # 금은 FRED 의 런던금(GOLDPMGBD228NLBM)이 2023년에 끊겼다. 과거 값은 아직
    # 주지만 최신값이 없어 머리에 띄우면 몇 년 전 값이 오늘 시세로 읽힌다.
    # 그래서 stooq 의 XAU/USD 를 먼저 보고, 안 되면 FRED 로 물러선다.
    {"key": "gold", "name": "금", "unit": "달러", "step": False,
     "stooq": "xauusd", "note": "국제 금 현물 (트로이온스)",
     "fallback": {"name": "금(옛 자료)", "step": False,
                  "note": "stooq 를 못 받아 FRED 런던금으로 대신함 — "
                          "2023년에 끊긴 계열이라 최신값이 아닐 수 있다",
                  "fred": ["GOLDPMGBD228NLBM", "GOLDAMGBD228NLBM"]}},
]

STOOQ_CSV = "https://stooq.com/q/d/l/?s={sym}&i=d"


def fetch_stooq(sym: str, start: dt.date) -> dict:
    """stooq 도 키 없이 CSV 를 준다. Date,Open,High,Low,Close 로 온다."""
    r = requests.get(STOOQ_CSV.format(sym=sym),
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            d = dt.date.fromisoformat(str(row.get("Date", "")).strip())
        except (ValueError, AttributeError):
            continue
        if d < start:
            continue
        try:
            out[d.strftime("%Y%m%d")] = float(row["Close"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 받아오기
# =============================================================================

def fetch_fred(series_id: str, start: dt.date) -> dict:
    """
    FRED 는 키 없이 CSV 를 준다. {YYYYMMDD: 값}.

    쉬는 날은 '.' 으로 온다. 그런 줄은 버린다 — 0 으로 채우면 금리가 0% 로
    떨어진 것처럼 보인다.
    """
    r = requests.get(FRED_CSV.format(id=series_id),
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        cols = list(row)
        if len(cols) < 2:
            continue
        day, val = row[cols[0]], row[cols[1]]
        try:
            d = dt.date.fromisoformat(day.strip())
        except (ValueError, AttributeError):
            continue
        if d < start:
            continue
        try:
            out[d.strftime("%Y%m%d")] = float(val)
        except (TypeError, ValueError):
            continue                      # '.' — 값이 없는 날
    return out


def fetch_ecos(spec: dict, start: dt.date, end: dt.date) -> dict:
    """한국은행 ECOS. 키가 없으면 빈 dict — 그 지표만 빠진다."""
    key = os.environ.get("ECOS_KEY", "").strip()
    if not key:
        log("::warning::ECOS_KEY 가 없습니다 — 한국 기준금리는 비웁니다. "
            "발급: https://ecos.bok.or.kr/api  →  저장소 Secrets 에 ECOS_KEY")
        return {}
    url = ECOS_URL.format(key=key, stat=spec["stat"], cycle=spec["cycle"],
                          start=start.strftime("%Y%m%d"),
                          end=end.strftime("%Y%m%d"), item=spec["item"])
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if "StatisticSearch" not in body:
        # ECOS 는 오류도 200 으로 준다. 키가 틀렸거나 통계표 코드가 틀린 경우다.
        log(f"::warning::ECOS 응답에 자료가 없습니다: {str(body)[:200]}")
        return {}
    out = {}
    for row in body["StatisticSearch"].get("row", []):
        day, val = row.get("TIME", ""), row.get("DATA_VALUE")
        if not re.fullmatch(r"\d{8}", day or ""):
            continue
        try:
            out[day] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def fetch_kospi(years: int = YEARS) -> dict:
    """
    코스피 일별 종가. 사건 이후 기록을 내려면 지수가 사건보다 길어야 한다.

    시장 신호는 3년만 모은다 (화면에 그 정도면 충분하다). 여기서는 표본을
    늘리려고 따로 10년을 받아 store/ 에만 둔다.
    """
    try:
        from pykrx import stock
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::pykrx 를 쓸 수 없습니다 ({type(e).__name__}: {e})")
        return {}
    # 10년을 한 번에 달라고 했더니 빈 표가 왔다 (실제로 0일치가 나왔다).
    # KRX 가 긴 구간을 잘라 버리는 것으로 보여, 해마다 나눠 받아 붙인다.
    # 한 해가 비어도 나머지는 남는다.
    end = dt.date.today()
    out, empty = {}, []
    for y in range(end.year - years, end.year + 1):
        a = dt.date(y, 1, 1).strftime("%Y%m%d")
        b = min(dt.date(y, 12, 31), end).strftime("%Y%m%d")
        try:
            df = stock.get_index_ohlcv_by_date(a, b, "1001")
        except Exception as e:                   # noqa: BLE001
            log(f"  코스피 {y}년 실패 ({type(e).__name__}: {e})")
            continue
        if df is None or df.empty:
            empty.append(str(y))
            continue
        for idx, row in df.iterrows():
            try:
                close = float(row["종가"])
            except (KeyError, TypeError, ValueError):
                continue
            if close > 0:
                out[idx.strftime("%Y%m%d")] = close
    if empty:
        log(f"  코스피 빈 해: {', '.join(empty)}")
    if not out:
        log("::warning::코스피를 하나도 받지 못했습니다 — 이후 기록이 비게 됩니다")
    return out


# =============================================================================
# 사건과 이후 기록
# =============================================================================

def changes(points: dict) -> list:
    """
    값이 바뀐 날. 정책금리는 바뀐 날이 곧 결정일이다.

    소수점 오차로 '바뀐 것처럼' 보이는 경우가 있어 0.001 미만은 같은 값으로 본다.
    """
    days = sorted(points)
    out, prev = [], None
    for d in days:
        v = points[d]
        if prev is not None and abs(v - prev) >= 0.001:
            out.append({"date": d, "from": round(prev, 4), "to": round(v, 4),
                        "dir": "인상" if v > prev else "인하"})
        prev = v
    return out


def after_returns(events: list, kospi: dict, spans=AFTER) -> dict:
    """
    사건 뒤 코스피가 어땠는지. **서술만 한다.**

    표본 수를 반드시 함께 낸다. 아홉 번 중 여섯 번은 '자주'가 아니라 '아홉 번 중
    여섯 번'이다. 표본이 3개 미만이면 통계를 내지 않는다 — 두 번 중 두 번 올랐다는
    말은 아무것도 알려주지 않으면서 확신만 준다.
    """
    if not kospi or not events:
        return {}
    days = sorted(kospi)
    pos = {d: i for i, d in enumerate(days)}

    def ret(day: str, n: int):
        # 사건일이 휴장일 수 있다. 그 다음 거래일을 기준으로 잡는다.
        i = pos.get(day)
        if i is None:
            later = [d for d in days if d >= day]
            if not later:
                return None
            i = pos[later[0]]
        if i + n >= len(days):
            return None
        a, b = kospi[days[i]], kospi[days[i + n]]
        return (b / a - 1) * 100 if a else None

    out = {}
    for direction in ("인상", "인하"):
        picked = [e for e in events if e["dir"] == direction]
        row = {"n": len(picked), "spans": {}}
        for n in spans:
            vals = [v for v in (ret(e["date"], n) for e in picked) if v is not None]
            if len(vals) < 3:
                continue
            vals.sort()
            mid = vals[len(vals) // 2] if len(vals) % 2 else \
                (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
            row["spans"][str(n)] = {
                "표본": len(vals),
                "상승": sum(1 for v in vals if v > 0),
                "중앙값": round(mid, 2),
            }
        if row["spans"]:
            out[direction] = row
    return out


def thin(points: dict, keep: int = CHART_POINTS) -> list:
    """
    차트용으로 솎는다. [[YYYYMMDD, 값], ...]

    계단 모양(금리)은 바뀐 지점을 남겨야 하므로 여기서 쓰지 않는다.
    """
    days = sorted(points)
    if len(days) <= keep:
        return [[d, points[d]] for d in days]
    step = len(days) / keep
    idx = sorted({int(i * step) for i in range(keep)} | {len(days) - 1})
    return [[days[i], points[days[i]]] for i in idx]


def step_points(points: dict, events: list) -> list:
    """계단 모양은 시작점·변화점·끝점만 있으면 그대로 그려진다."""
    days = sorted(points)
    if not days:
        return []
    keys = [days[0]] + [e["date"] for e in events] + [days[-1]]
    seen, out = set(), []
    for d in keys:
        if d in points and d not in seen:
            seen.add(d)
            out.append([d, points[d]])
    return sorted(out)


# =============================================================================

def collect(years: int = YEARS) -> dict:
    end = dt.date.today()
    start = end - dt.timedelta(days=int(365.25 * years) + 10)
    kospi = fetch_kospi(years)
    log(f"[수집] 코스피 {len(kospi)}일치")

    out, failed = [], []
    for spec in SERIES:
        spec = dict(spec)
        name = spec["name"]
        try:
            if "stooq" in spec:
                pts = fetch_stooq(spec["stooq"], start)
            elif "fred" in spec:
                pts = fetch_fred(spec["fred"], start)
            else:
                pts = fetch_ecos(spec["ecos"], start, end)
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{name} 실패 ({type(e).__name__}: {e})")
            pts = {}

        # 원본을 못 받았을 때만 대용을 쓴다. 후보를 차례로 시도하고, 쓰게 되면
        # 이름과 설명을 대용의 것으로 바꿔 단다 — 대용을 원본 이름으로 내보내면
        # 보는 사람이 알 방법이 없다.
        if not pts and spec.get("fallback"):
            fb = spec["fallback"]
            for fid in fb["fred"]:
                try:
                    pts = fetch_fred(fid, start)
                except Exception as e:           # noqa: BLE001
                    log(f"  {name} 대용 후보 실패 {fid} ({type(e).__name__})")
                    continue
                if pts:
                    spec["name"] = name = fb["name"]
                    spec["note"] = f"{fb['note']} ({fid})"
                    spec["step"] = fb.get("step", spec["step"])
                    log(f"::warning::{name} — 원본 대신 대용을 씁니다: {fid}")
                    break

        if not pts:
            failed.append(name)
            log(f"  {name}: 없음")
            out.append({**{k: spec[k] for k in ("key", "name", "unit", "note")},
                        "points": [], "events": [], "after": {}, "last": None})
            continue

        evs = changes(pts) if spec["step"] else []
        chart = step_points(pts, evs) if spec["step"] else thin(pts)
        last_day = max(pts)
        item = {k: spec[k] for k in ("key", "name", "unit", "note")}
        item.update({
            "points": chart,
            "events": evs[-24:],                 # 최근 것만. 옛날 것은 통계로 남는다
            "after": after_returns(evs, kospi),
            "last": pts[last_day],
            "last_date": last_day,
            "span": [min(pts), last_day],
        })
        out.append(item)
        log(f"  {name}: {len(pts)}일치 · 변화 {len(evs)}회 · "
            f"최근 {pts[last_day]}{spec['unit']} ({last_day})")

    return {"as_of": end.strftime("%Y%m%d"), "years": years,
            "kospi_days": len(kospi), "series": out, "failed": failed}


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def dump() -> int:
    start = dt.date.today() - dt.timedelta(days=400)
    for spec in SERIES:
        log(f"\n===== {spec['name']}")
        try:
            if "stooq" in spec:
                r = requests.get(STOOQ_CSV.format(sym=spec["stooq"]),
                                 headers={"User-Agent": UA}, timeout=TIMEOUT)
                log(f"  stooq {spec['stooq']} → {r.status_code}, {len(r.text)}자")
                log("  앞 3줄: " + " | ".join(r.text.splitlines()[:3]))
            elif "fred" in spec:
                r = requests.get(FRED_CSV.format(id=spec["fred"]),
                                 headers={"User-Agent": UA}, timeout=TIMEOUT)
                log(f"  FRED {spec['fred']} → {r.status_code}, {len(r.text)}자")
                log("  앞 3줄: " + " | ".join(r.text.splitlines()[:3]))
            else:
                pts = fetch_ecos(spec["ecos"], start, dt.date.today())
                log(f"  ECOS → {len(pts)}건 {sorted(pts)[:3]}")
        except Exception as e:                   # noqa: BLE001
            log(f"  실패: {type(e).__name__}: {e}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="거시 지표 — 흐름·사건·이후 기록")
    p.add_argument("--years", type=int, default=YEARS)
    p.add_argument("--dump", action="store_true")
    a = p.parse_args(argv)
    if a.dump:
        return dump()

    payload = collect(a.years)
    save(payload)
    for s in payload["series"]:
        if not s["events"]:
            continue
        log(f"\n[{s['name']}] 최근 변화")
        for e in s["events"][-4:]:
            log(f"  {e['date']}  {e['dir']}  {e['from']} → {e['to']}{s['unit']}")
        for d, row in (s["after"] or {}).items():
            for n, v in row["spans"].items():
                log(f"  {d} {row['n']}회 → {n}거래일 뒤 상승 "
                    f"{v['상승']}/{v['표본']}회, 중앙값 {v['중앙값']:+.2f}%")
    if payload["failed"]:
        log(f"::warning::받지 못한 지표: {', '.join(payload['failed'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
