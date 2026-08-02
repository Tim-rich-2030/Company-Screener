# -*- coding: utf-8 -*-
"""
시장 신호 카드 — 코스피·코스닥 지수의 위치를 숫자 몇 개로 요약한다.

개별 종목을 보기 전에 "지금 시장이 어디쯤인가"를 먼저 보려는 용도다. 판단은
전부 **일봉 종가**만으로 한다. 장중 값을 섞으면 같은 날에도 볼 때마다 답이
달라져 기록으로 남기지 못한다.

보는 것은 네 가지다.
  1) 20일선 대비 이격도 — 평균에서 얼마나 떨어져 있나
  2) 20일선의 방향     — 그 평균 자체가 오르고 있나
  3) 60거래일 밴드 위치 — 최근 석 달 범위에서 어디쯤인가
  4) 코스닥/코스피 비율 — 둘 중 어디에 힘이 실려 있나

숫자는 사실이고 '상승·하락·횡보' 같은 말은 아래 상수로 그은 임의의 선이다.
기준을 바꾸고 싶으면 상수만 고치면 된다.

    python market_signal.py                 # 최신 영업일 기준 계산
    python market_signal.py --date 2026-07-30
    python market_signal.py --collect       # 지수 시세를 받아 저장까지
"""
from __future__ import annotations

import io
import os
import re
import sys
import json
import argparse
import datetime as dt

import requests

# --- 계산 기준 (여기만 고치면 판정이 바뀐다) ---------------------------------
SMA_WINDOW = 20          # 이동평균 일수
SLOPE_DAYS = 5           # 20일선 기울기를 재는 구간
BAND_DAYS = 60           # 고점·저점 밴드 구간
YEARS = 3                # 수집할 과거 기간

# 20일선이 5일 동안 이만큼도 못 움직이면 방향이 없다고 본다 (%)
TREND_FLAT_PCT = 0.5
# 코스닥/코스피 비율의 이격도가 이 안이면 어느 쪽도 우위가 아니라고 본다 (%)
LEAD_FLAT_PCT = 0.5

INDICES = {"코스피": {"krx": "1001", "naver": "KOSPI"},
           "코스닥": {"krx": "2001", "naver": "KOSDAQ"}}

STORE_PATH = os.path.join("store", "market_signal.json")
# GitHub Pages 는 docs/ 아래만 서빙한다. 화면이 fetch 할 수 있게 사본을 둔다.
DOCS_PATH = os.path.join("docs", "market_signal.json")

NAVER_CHART = ("https://fchart.stock.naver.com/sise.nhn"
               "?timeframe=day&count={count}&requestType=0&symbol={symbol}")
ITEM_RE = re.compile(r'data="(\d{8})\|([\d.]*)\|([\d.]*)\|([\d.]*)\|([\d.]*)\|')
TIMEOUT = 20


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 수집
# =============================================================================

def _fetch_pykrx(spec: dict, start: dt.date, end: dt.date) -> dict:
    """pykrx 로 지수 일봉 종가. KRX 로그인이 필요해지면 빈 dict."""
    try:
        from pykrx import stock
    except ImportError:
        return {}
    try:
        df = stock.get_index_ohlcv_by_date(start.strftime("%Y%m%d"),
                                           end.strftime("%Y%m%d"), spec["krx"])
    except Exception as exc:
        log(f"  pykrx 실패: {type(exc).__name__}: {exc}")
        return {}
    if df is None or df.empty or "종가" not in df.columns:
        return {}
    out = {}
    for idx, close in df["종가"].items():
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[idx.strftime("%Y%m%d")] = round(value, 2)
    return out


def _fetch_naver(spec: dict, days: int) -> dict:
    """
    네이버 차트로 지수 일봉 종가.

    KRX 는 2026년부터 로그인을 요구해서 pykrx 가 통째로 막히는 일이 있다
    (이 저장소의 종목 수집도 같은 이유로 한 번 멈췄다). 네이버 차트는 로그인이
    없고 한 번 호출로 수천 일치를 준다.
    """
    url = NAVER_CHART.format(count=days, symbol=spec["naver"])
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        text = resp.content.decode("euc-kr", "replace")
    except Exception as exc:
        log(f"  네이버 실패: {type(exc).__name__}: {exc}")
        return {}
    out = {}
    for ymd, _o, _h, _l, close in ITEM_RE.findall(text):
        try:
            value = float(close)
        except ValueError:
            continue
        if value > 0:
            out[ymd] = round(value, 2)
    return out


def collect(years: int = YEARS) -> dict:
    """지수별 {YYYYMMDD: 종가}. pykrx 를 먼저 쓰고 막히면 네이버로 간다."""
    end = dt.date.today()
    start = end - dt.timedelta(days=int(365.25 * years) + 10)
    series, sources = {}, {}
    for name, spec in INDICES.items():
        log(f"[수집] {name}")
        data = _fetch_pykrx(spec, start, end)
        source = "pykrx"
        if len(data) < SMA_WINDOW + SLOPE_DAYS:
            data = _fetch_naver(spec, int(365.25 * years * 0.75) + 60)
            source = "naver"
        if len(data) < SMA_WINDOW + SLOPE_DAYS:
            raise SystemExit(f"{name} 시세를 받지 못했습니다 "
                             f"({len(data)}일치) — pykrx·네이버 모두 실패")
        series[name] = data
        sources[name] = source
        log(f"  {len(data)}일치 ({source}) {min(data)} ~ {max(data)}")
    return {"series": series, "sources": sources}


# =============================================================================
# 계산 — 전부 종가 기준
# =============================================================================

def _upto(series: dict, as_of: str) -> list:
    """as_of 이하 날짜를 오름차순 (날짜, 종가) 로."""
    return sorted(((d, v) for d, v in series.items() if d <= as_of))


def trend_label(slope_pct: float) -> str:
    if slope_pct >= TREND_FLAT_PCT:
        return "상승"
    if slope_pct <= -TREND_FLAT_PCT:
        return "하락"
    return "횡보"


def sma_at(rows: list, window: int = SMA_WINDOW, offset: int = 0):
    """rows 끝에서 offset 만큼 거슬러 올라간 지점의 단순이동평균."""
    end = len(rows) - offset
    if end < window:
        return None
    vals = [v for _d, v in rows[end - window:end]]
    return sum(vals) / window


def analyse(series: dict, as_of: str) -> dict | None:
    """한 지수의 그날 기준 계산값."""
    rows = _upto(series, as_of)
    if len(rows) < SMA_WINDOW + SLOPE_DAYS:
        return None
    date, close = rows[-1]
    sma = sma_at(rows)
    prev_sma = sma_at(rows, offset=SLOPE_DAYS)

    band = rows[-BAND_DAYS:]
    high = max(v for _d, v in band)
    low = min(v for _d, v in band)
    # 밴드 폭이 0 이면(전 구간 같은 값) 위치를 말할 수 없다
    pos = (close - low) / (high - low) * 100 if high > low else None

    slope = ((sma - prev_sma) / prev_sma * 100) if prev_sma else None
    return {
        "date": date,
        "close": round(close, 2),
        "sma20": round(sma, 2),
        "disparity": round((close - sma) / sma * 100, 2),
        "slope_pct": round(slope, 2) if slope is not None else None,
        "trend": trend_label(slope) if slope is not None else "판단 불가",
        "band_days": len(band),
        "band_high": round(high, 2),
        "band_low": round(low, 2),
        "band_pos": round(pos, 1) if pos is not None else None,
    }


def analyse_ratio(kosdaq: dict, kospi: dict, as_of: str) -> dict | None:
    """
    코스닥 ÷ 코스피 비율의 이격도로 어느 쪽에 힘이 실렸는지 본다.

    지수 절대값은 단위가 달라 그대로 못 견준다. 비율이 자기 20일 평균보다 높다는
    것은 최근 코스닥이 코스피보다 더 올랐거나 덜 빠졌다는 뜻이다.
    """
    common = sorted(set(kosdaq) & set(kospi))
    ratio = {d: kosdaq[d] / kospi[d] for d in common if kospi[d]}
    rows = _upto(ratio, as_of)
    if len(rows) < SMA_WINDOW:
        return None
    date, value = rows[-1]
    sma = sma_at(rows)
    disparity = (value - sma) / sma * 100
    if disparity >= LEAD_FLAT_PCT:
        leader = "코스닥 우위"
    elif disparity <= -LEAD_FLAT_PCT:
        leader = "코스피 우위"
    else:
        leader = "비슷"
    return {"date": date, "value": round(value, 5), "sma20": round(sma, 5),
            "disparity": round(disparity, 2), "leader": leader}


def compute(payload: dict, as_of: str = None) -> dict:
    """수집본에서 as_of(기본: 가장 최근 영업일) 기준 계산값을 만든다."""
    series = payload["series"]
    latest = min(max(s) for s in series.values())     # 두 지수 다 있는 마지막 날
    as_of = (as_of or latest).replace("-", "")
    if as_of > latest:
        as_of = latest

    indices = {}
    for name, data in series.items():
        got = analyse(data, as_of)
        if got is None:
            raise SystemExit(f"{name}: {as_of} 기준으로 계산할 데이터가 모자랍니다")
        indices[name] = got
    ratio = analyse_ratio(series["코스닥"], series["코스피"], as_of)
    return {
        "as_of": indices["코스피"]["date"],
        "indices": indices,
        "ratio": ratio,
        "params": {"sma": SMA_WINDOW, "slope_days": SLOPE_DAYS,
                   "band_days": BAND_DAYS, "trend_flat_pct": TREND_FLAT_PCT,
                   "lead_flat_pct": LEAD_FLAT_PCT},
    }


# =============================================================================
# 저장 / 출력
# =============================================================================

def save(payload: dict) -> None:
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
            fp.write("\n")
        log(f"[저장] {path}")


def load() -> dict:
    for path in (STORE_PATH, DOCS_PATH):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fp:
                return json.load(fp)
    raise SystemExit(f"{STORE_PATH} 가 없습니다. --collect 로 먼저 받으세요.")


def fmt_signed(v, digits=2):
    return "–" if v is None else f"{v:+.{digits}f}"


def report(result: dict) -> None:
    print(f"\n기준일 {result['as_of']}  "
          f"(이동평균 {SMA_WINDOW}일 · 기울기 {SLOPE_DAYS}일 · 밴드 {BAND_DAYS}일)")
    print("=" * 62)
    for name, d in result["indices"].items():
        print(f"\n[{name}]")
        print(f"  종가            {d['close']:>12,.2f}")
        print(f"  20일선          {d['sma20']:>12,.2f}")
        print(f"  이격도          {fmt_signed(d['disparity']):>12} %")
        print(f"  20일선 기울기   {fmt_signed(d['slope_pct']):>12} %  "
              f"({SLOPE_DAYS}일) → {d['trend']}")
        print(f"  {d['band_days']}일 고점       {d['band_high']:>12,.2f}")
        print(f"  {d['band_days']}일 저점       {d['band_low']:>12,.2f}")
        pos = d["band_pos"]
        print(f"  밴드 내 위치    {'–' if pos is None else f'{pos:>12.1f} %'}")
    r = result.get("ratio")
    print("\n[코스닥 ÷ 코스피]")
    if not r:
        print("  계산 불가 (겹치는 날짜 부족)")
    else:
        print(f"  비율            {r['value']:>12.5f}")
        print(f"  20일선          {r['sma20']:>12.5f}")
        print(f"  이격도          {fmt_signed(r['disparity']):>12} %  → {r['leader']}")
    print()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="코스피·코스닥 시장 신호")
    p.add_argument("--date", help="기준일 (YYYY-MM-DD). 비우면 최신 영업일")
    p.add_argument("--collect", action="store_true",
                   help="지수 시세를 새로 받아 store/ 에 저장")
    args = p.parse_args(argv)

    if args.collect:
        payload = collect()
        payload["computed"] = compute(payload)
        save(payload)
        report(payload["computed"])
        return 0

    payload = load()
    report(compute(payload, args.date))
    return 0


if __name__ == "__main__":
    sys.exit(main())
