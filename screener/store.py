# -*- coding: utf-8 -*-
"""
시계열 저장소.

종목 하나당 파일 하나(`store/facts/{종목코드}.json`)로 둔다. 한 덩어리 파일이면
매 실행마다 전체를 다시 쓰게 되고 git diff가 통째로 잡혀서, 어느 종목이 언제
갱신됐는지 추적할 수 없다.

저장하는 것은 **원천 숫자뿐**이다. PBR·PER 같은 파생 지표는 저장하지 않고
사이트를 만들 때마다 다시 계산한다. 지표 정의를 바꿨을 때 과거 분기에 낡은 값이
남아 있으면 시계열이 조용히 섞이기 때문이다.

    {
      "code": "005930", "name": "삼성전자",
      "quarters": {
        "2025Q3": {
          "매출액": 79000000000000, "영업이익": ..., "순이익": ...,
          "지배주주순이익": ..., "자본총계": ..., "지배주주지분": ...,
          "자산총계": ..., "부채총계": ...,
          "종가": 70000, "상장주식수": 5969782550, "시가총액": ...,
          "price_date": "20250930", "price_src": "naver",
          "rcept_no": "20251114000123", "fs_div": "CFS",
          "updated": "2026-07-29T04:00:00Z"
        }
      }
    }
"""
from __future__ import annotations

import os
import re
import json
import glob
import datetime as dt

from . import config

QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")

# 분기 원천 계정 (여기에 있는 키만 병합 대상)
FACT_KEYS = (
    "매출액", "영업이익", "순이익", "지배주주순이익",
    "자본총계", "지배주주지분", "자산총계", "부채총계",
    "종가", "상장주식수", "시가총액",
)


def quarter_key(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def parse_quarter(key: str) -> tuple[int, int] | None:
    m = QUARTER_RE.match(key or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def sort_quarters(keys, newest_first: bool = True) -> list:
    """분기 라벨을 시간순으로 정렬한다. 문자열 정렬은 2025Q10 같은 값에서 깨진다."""
    valid = [k for k in keys if parse_quarter(k)]
    return sorted(valid, key=parse_quarter, reverse=newest_first)


def quarter_end_date(year: int, quarter: int) -> dt.date:
    """분기말 달력 날짜 (영업일 보정은 주가 조회 쪽에서 한다)."""
    return {1: dt.date(year, 3, 31), 2: dt.date(year, 6, 30),
            3: dt.date(year, 9, 30), 4: dt.date(year, 12, 31)}[quarter]


def _path(code: str) -> str:
    return os.path.join(config.FACTS_DIR, f"{code}.json")


def load(code: str) -> dict:
    try:
        with open(_path(code), encoding="utf-8") as fp:
            return json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"code": code, "name": "", "quarters": {}}


def load_all() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(config.FACTS_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fp:
                out.append(json.load(fp))
        except json.JSONDecodeError:
            continue
    return out


def save(record: dict) -> str:
    os.makedirs(config.FACTS_DIR, exist_ok=True)
    record["quarters"] = {
        k: record["quarters"][k]
        for k in sort_quarters(record.get("quarters", {}))[:config.KEEP_QUARTERS]
    }
    path = _path(record["code"])
    with open(path, "w", encoding="utf-8") as fp:
        # 분기를 최신순으로 고정해 두면 git diff가 읽을 만해진다
        json.dump(record, fp, ensure_ascii=False, indent=1, sort_keys=False)
        fp.write("\n")
    return path


def merge_quarter(record: dict, qkey: str, values: dict, meta: dict = None) -> bool:
    """
    한 분기에 값을 채워 넣는다. 이미 있는 값은 덮어쓰지 않는다.

    같은 분기를 여러 보고서가 담는다(3분기 실적은 3분기보고서에도, 다음 해 3분기
    보고서의 전년 동기에도 들어 있다). 먼저 들어온 값 = 더 원본에 가까운 보고서의
    값이므로 그대로 둔다. 덮어쓰면 재작성 이전/이후 값이 실행 순서에 따라 뒤섞인다.

    반환: 실제로 새로 채운 값이 있으면 True.
    """
    if not parse_quarter(qkey):
        return False
    slot = record.setdefault("quarters", {}).setdefault(qkey, {})
    changed = False
    for key, val in (values or {}).items():
        if key not in FACT_KEYS or val is None:
            continue
        if slot.get(key) is None:
            slot[key] = val
            changed = True
    if changed:
        for key, val in (meta or {}).items():
            slot.setdefault(key, val)
        slot["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return changed


def set_price(record: dict, qkey: str, close: float, shares: float,
              price_date: str, source: str) -> bool:
    """분기말 주가 스냅샷. 시가총액은 그 시점 주식수로 계산해 함께 굳힌다."""
    slot = record.setdefault("quarters", {}).setdefault(qkey, {})
    if slot.get("종가") is not None:
        return False                       # 과거 스냅샷은 다시 건드리지 않는다
    slot["종가"] = close
    if shares:
        slot["상장주식수"] = shares
        slot["시가총액"] = close * shares
    slot["price_date"] = price_date
    slot["price_src"] = source
    slot["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return True


def missing_price_quarters(record: dict) -> list:
    """주가 스냅샷이 아직 없는 분기 목록 (최신순)."""
    return [k for k in sort_quarters(record.get("quarters", {}))
            if record["quarters"][k].get("종가") is None]


# =============================================================================
# 실행 상태 — 어떤 공시를 이미 처리했는가
# =============================================================================

def load_state() -> dict:
    try:
        with open(config.STATE_PATH, encoding="utf-8") as fp:
            state = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("seen_rcept", [])
    state.setdefault("last_run", "")
    state.setdefault("pending", [])
    return state


def save_state(state: dict, keep: int = 20000) -> None:
    os.makedirs(config.STORE_DIR, exist_ok=True)
    # 접수번호는 시간순으로 증가하므로 최근 것만 남겨도 중복 판정에 문제가 없다
    state["seen_rcept"] = sorted(set(state.get("seen_rcept", [])))[-keep:]
    with open(config.STATE_PATH, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=1)
        fp.write("\n")
