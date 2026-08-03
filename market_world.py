# -*- coding: utf-8 -*-
"""
해외 지수 — 나스닥 · S&P 500 · 다우.

머리에 코스피·코스닥과 나란히 세워 두려고 받는다. **종가만** 온다.

    FRED 는 키 없이 CSV 를 준다. 대신 시가·고가·저가가 없어서 봉을 못 그린다.
    선으로 그리고, 화면에 '종가·선' 이라고 적는다.

    그리고 **하루 늦다.** 미국 장은 한국 시간으로 새벽에 끝나고 FRED 가 그것을
    올리기까지 시차가 있다. 오늘 아침에 받으면 어제 종가다. 그래서 값 옆에
    날짜를 같이 내보낸다 — 코스피 옆에 나란히 두면 같은 날짜로 읽힌다.

    python market_world.py
    python market_world.py --years 3
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime as dt

import market_macro

YEARS = 3                 # 화면 차트가 쓰는 만큼만
THIN = 400                # 점이 이보다 많으면 솎는다 (폰에서 선이 뭉갠다)

SERIES = [
    {"key": "nasdaq", "name": "나스닥", "fred": "NASDAQCOM"},
    {"key": "sp500", "name": "S&P 500", "fred": "SP500"},
    {"key": "dow", "name": "다우", "fred": "DJIA"},
]

STORE_PATH = os.path.join("store", "market_world.json")
DOCS_PATH = os.path.join("docs", "market_world.json")


def log(msg: str) -> None:
    print(msg, flush=True)


def thin(points: list, keep: int = THIN) -> list:
    """
    점을 솎는다. **마지막 점은 반드시 남긴다** — 그게 지금 값이다.

    앞에서부터 일정 간격으로 버리면 마지막이 잘려 머리의 숫자와 차트의 끝이
    어긋난다.
    """
    if len(points) <= keep:
        return points
    step = len(points) / keep
    out = [points[int(i * step)] for i in range(keep)]
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def build(spec: dict, pts: dict) -> dict:
    days = sorted(pts)
    if not days:
        return {}
    last, prev = pts[days[-1]], pts[days[-2]] if len(days) > 1 else None
    return {
        "key": spec["key"],
        "name": spec["name"],
        "date": days[-1],
        "last": round(last, 2),
        # 전일 대비. 하루 늦게 오는 자료라 '오늘 등락률'이 아니다.
        "chg": None if not prev else round((last - prev) / prev * 100, 2),
        "points": thin([[d, round(pts[d], 2)] for d in days]),
        "note": "종가 · FRED",
    }


def collect(years: int = YEARS) -> dict:
    start = dt.date.today() - dt.timedelta(days=int(365.25 * years) + 10)
    out, failed = [], []
    for spec in SERIES:
        try:
            pts = market_macro.fetch_fred(spec["fred"], start)
        except Exception as e:                   # noqa: BLE001
            log(f"  {spec['name']} 실패 ({type(e).__name__}: {e})")
            failed.append(spec["name"])
            continue
        row = build(spec, pts)
        if not row:
            log(f"  {spec['name']}: 값이 없습니다")
            failed.append(spec["name"])
            continue
        out.append(row)
        log(f"  {spec['name']:8} {row['last']:>12,.2f} "
            f"({row['date']}) · 점 {len(row['points'])}개")
    if not out:
        raise SystemExit("해외 지수를 하나도 받지 못했습니다")
    return {"as_of": dt.date.today().strftime("%Y%m%d"),
            "source": "FRED", "series": out, "failed": failed}


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="해외 지수 (나스닥·S&P500·다우)")
    p.add_argument("--years", type=int, default=YEARS)
    a = p.parse_args(argv)
    payload = collect(a.years)
    save(payload)
    return 1 if payload["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
