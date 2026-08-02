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
PCTILE_DAYS = 250        # 이격도 백분위를 매길 기간 (약 1년)
PCTILE_MIN = 60          # 이만큼도 없으면 백분위를 매기지 않는다
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

def krx_credentials() -> bool:
    """KRX 로그인 정보가 환경에 있는지. pykrx 는 KRX_ID/KRX_PW 를 직접 읽는다."""
    return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))


def check_krx() -> int:
    """
    방금 끝난 수집이 실제로 KRX 에서 받아왔는지 확인한다.

    처음에는 여기서 따로 로그인해 조회까지 해봤다. 그런데 그러면 한 번 실행에
    로그인을 두 번 하게 된다 — 수집 때 한 번, 점검 때 또 한 번. 로그인 빈도를
    괜히 두 배로 올리는 데다, 답은 이미 수집 결과에 들어 있다. 네이버로
    넘어갔다면 그게 곧 'KRX 에서 못 받았다'는 뜻이다.

    그래서 새로 접속하지 않고 저장된 출처만 본다.
    """
    if not krx_credentials():
        log("::warning::KRX_ID / KRX_PW 가 없습니다 — 네이버 차트로 받습니다. "
            "pykrx 를 쓰려면 저장소 Secrets 에 두 값을 넣으세요.")
        return 0                      # 안 넣은 것은 선택이지 오류가 아니다
    try:
        payload = load()
    except SystemExit:
        log("::error::수집 결과가 없어 KRX 사용 여부를 확인할 수 없습니다")
        return 1
    sources = payload.get("sources") or {}
    fell_back = sorted(k for k, v in sources.items() if v != "pykrx")
    if fell_back:
        log(f"::error::KRX 계정이 있는데 {', '.join(fell_back)} 를 "
            f"{sources.get(fell_back[0])} 로 받았습니다 — KRX 로그인이 안 됐다는 뜻입니다. "
            "KRX 장애면 잠시 뒤 정상으로 돌아옵니다. 계속 이러면 계정을 확인하세요 "
            "(데이터 자체는 정상 수집됐습니다)")
        return 1
    log(f"KRX 정상 — {', '.join(sources)} 모두 pykrx 로 받았습니다")
    return 0


def _fetch_pykrx(spec: dict, start: dt.date, end: dt.date) -> dict:
    """pykrx 로 지수 일봉 종가. KRX 로그인이 안 되면 빈 dict."""
    try:
        # import 시점에 KRX 로그인을 시도하므로 ImportError 만 잡으면 안 된다.
        # KRX 가 죽어 있으면 여기서 예외가 나는데, 그건 네이버로 넘어갈 상황이지
        # 수집 전체를 멈출 상황이 아니다.
        from pykrx import stock
    except Exception as exc:
        log(f"  pykrx 불러오기 실패: {type(exc).__name__}")
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
    has_krx = krx_credentials()
    if not has_krx:
        log("KRX_ID / KRX_PW 없음 — 네이버 차트로 받습니다")
    series, sources = {}, {}
    for name, spec in INDICES.items():
        log(f"[수집] {name}")
        data = _fetch_pykrx(spec, start, end) if has_krx else {}
        source = "pykrx"
        if len(data) < SMA_WINDOW + SLOPE_DAYS:
            if has_krx:
                # 계정을 넣어 뒀는데 못 받았다는 건 계정 쪽 문제일 수 있다.
                # 데이터는 네이버로 채우되 조용히 넘어가지는 않는다.
                log(f"::warning::{name}: KRX 계정이 있는데 pykrx 가 "
                    f"{len(data)}일치만 줬습니다 — 네이버로 대체합니다")
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


def disparity_percentile(rows: list, window: int = SMA_WINDOW,
                         lookback: int = PCTILE_DAYS):
    """
    오늘 이격도가 최근 1년 이격도 분포에서 몇 번째인지 (0=가장 눌림, 100=가장 뜸).

    60일 고점·저점 안의 위치는 밴드가 넓어지면 못 쓴다. 2026년처럼 60일 사이에
    지수가 40% 움직이면 밴드 폭이 63%가 되고, 그 안의 '28.5%'는 아무것도 말해
    주지 않는다. 게다가 min-max 는 이상치 딱 두 날(최고·최저)이 눈금을 통째로
    정해 버려서, 극단에서는 0%·100% 에 붙어 버린다.

    백분위는 '지금 이 시장 기준으로' 얼마나 드문 상태인지를 말한다. 변동성이
    커지면 분포도 같이 넓어지므로 국면이 바뀌어도 뜻이 유지되고, 단위가 없어서
    코스피와 코스닥을 나란히 놓고 볼 수 있다.
    """
    if len(rows) < window + PCTILE_MIN:
        return None
    closes = [v for _d, v in rows]
    hist = []
    for i in range(window, len(closes)):
        sma = sum(closes[i - window:i]) / window
        if sma:
            hist.append((closes[i] - sma) / sma * 100)
    hist = hist[-lookback:]
    if len(hist) < PCTILE_MIN:
        return None
    today = hist[-1]
    return sum(1 for v in hist if v <= today) / len(hist) * 100


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
        "stretch_pct": (round(sp, 1) if (sp := disparity_percentile(rows))
                        is not None else None),
        "stretch_days": min(len(rows) - SMA_WINDOW, PCTILE_DAYS),
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


def source_label(sources: dict) -> str:
    """어디서 받은 값인지. 출처가 섞이면 둘 다 적는다."""
    kinds = sorted(set((sources or {}).values()))
    return "+".join(kinds) if kinds else ""


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
        "source": source_label(payload.get("sources")),
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
    src = result.get("source")
    print(f"\n기준일 {result['as_of']}  "
          f"(이동평균 {SMA_WINDOW}일 · 기울기 {SLOPE_DAYS}일 · 밴드 {BAND_DAYS}일"
          f"{' · 출처 ' + src if src else ''})")
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
        print(f"  밴드 내 위치    {'–' if pos is None else f'{pos:>12.1f} %'}"
              "   (밴드가 넓으면 뜻이 약해진다)")
        sp = d.get("stretch_pct")
        print(f"  이격도 백분위   {'–' if sp is None else f'{sp:>12.1f} %'}"
              f"   최근 {d.get('stretch_days', PCTILE_DAYS)}일 중")
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
    p.add_argument("--check-krx", action="store_true",
                   help="KRX 계정으로 실제 조회가 되는지 확인")
    args = p.parse_args(argv)

    if args.check_krx:
        return check_krx()

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
