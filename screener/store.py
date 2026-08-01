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
import statistics
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
    # 메타는 값이 이미 다 차 있어도 붙인다. changed 일 때만 붙이면, 나중에 새로
    # 생긴 메타(통화 등)가 이미 수집된 분기에는 영영 달라붙지 못한다. 두산밥캣이
    # 그랬다 — USD 공시인데 통화가 비어 있어 PBR 이 1,000배로 찍혔다.
    for key, val in (meta or {}).items():
        if val not in (None, ""):
            slot.setdefault(key, val)
    if changed:
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


# 앞뒤 분기가 다 있을 때: 이 배수를 넘게 튀면 공시 오류로 본다.
SHARES_JUMP_LIMIT = 20
# 한쪽 이웃밖에 없을 때(가장 최근·가장 오래된 분기)는 훨씬 크게 잡는다.
# 여기서 잘못 지우면 이웃 값을 끌어와 채우므로 '빈 값'이 아니라 '틀린 값'이 된다.
# 액면분할은 아무리 커도 50:1 수준이라(삼성전자 2018년) 200배면 안전하다.
SHARES_EDGE_LIMIT = 200


def _known_shares(record: dict) -> list:
    """주식수가 채워진 분기의 (분기, 값) 목록."""
    return [(q, s.get("상장주식수"))
            for q, s in record.get("quarters", {}).items()
            if s.get("상장주식수") and s["상장주식수"] > 0]


def _neighbour_shares(record: dict, qkey: str) -> list:
    """qkey 앞뒤로 가장 가까운, 주식수가 채워진 분기의 값."""
    quarters = sort_quarters(record.get("quarters", {}), newest_first=False)
    if qkey not in quarters:
        return []
    i = quarters.index(qkey)
    out = []
    for span in (range(i - 1, -1, -1), range(i + 1, len(quarters))):
        for j in span:
            val = record["quarters"][quarters[j]].get("상장주식수")
            if val and val > 0:
                out.append(val)
                break
    return out


def shares_look_wrong(record: dict, qkey: str, shares: float,
                      limit: float = SHARES_JUMP_LIMIT) -> bool:
    """
    다른 분기들과 자릿수가 어긋나는 주식수인지.

    액면분할·증자는 한쪽 이웃과는 값이 맞는 '계단'이다(카프로 2024Q2 에 4천만 →
    1억6900만). 반면 공시 오류는 한 분기만 솟은 '뾰족한 점'이다(LS에코에너지
    2025Q4 가 정확히 100만 배, 카프로 2023Q2 가 1000배). 그래서 양쪽 이웃 모두와
    어긋날 때만 오류로 본다 — 진짜 자본 변동을 오류로 몰지 않기 위해서다.
    """
    if not shares or shares <= 0:
        return False
    sides = _neighbour_shares(record, qkey)
    if not sides:
        return False                      # 견줄 이웃이 없으면 판단하지 않는다
    if len(sides) == 2:
        return all(max(shares, s) / min(shares, s) > limit for s in sides)
    # 이웃이 한쪽뿐인 가장 최근·가장 오래된 분기. 그 하나뿐인 이웃이 하필 틀린
    # 값이면 멀쩡한 분기가 같이 걸린다 — LS에코에너지 2026Q1 이 바로 앞 분기의
    # 오류 때문에 그랬다. 그래서 이웃 하나가 아니라 나머지 분기 전체의 중앙값과
    # 견준다. 오류는 한둘이고 정상값이 다수라 중앙값은 정상 쪽에 선다.
    others = [v for q, v in _known_shares(record) if q != qkey]
    ref = statistics.median(others) if others else sides[0]
    return max(shares, ref) / min(shares, ref) > SHARES_EDGE_LIMIT


def frozen_price_run(record: dict, min_quarters: int = 2) -> dict | None:
    """
    최신 분기부터 종가가 몇 분기째 한 원도 안 움직였는지.

    거래정지 종목은 마지막 체결가가 그대로 남아, 시세 조회가 어느 날짜를 물어도
    같은 값을 돌려준다. 그래서 분기말 종가가 여러 분기 연속 똑같아진다. 거래가
    살아 있는 종목이 분기말마다 정확히 같은 값으로 끝날 일은 사실상 없다.
    (카프로는 9분기째 3,660원, 금양은 5분기째 9,900원이다.)

    KRX 의 거래정지 목록을 받아오는 쪽이 정확하지만 로그인이 필요하다. 여기서는
    이미 가진 데이터만으로 판단하므로 '단정'이 아니라 '의심'까지만 말한다.
    반환값에 근거(몇 분기·얼마)를 담아 화면에 그대로 띄운다.
    """
    quarters = sort_quarters(record.get("quarters", {}))
    closes = [(q, record["quarters"][q].get("종가")) for q in quarters]
    closes = [(q, c) for q, c in closes if c]
    if len(closes) < min_quarters:
        return None
    price = closes[0][1]
    run = 0
    for _, close in closes:
        if close != price:
            break
        run += 1
    if run < min_quarters:
        return None
    return {"quarters": run, "close": price, "since": closes[run - 1][0]}


def drop_implausible_shares(record: dict, limit: float = SHARES_JUMP_LIMIT) -> list:
    """
    이미 저장된 주식수 중 이웃과 자릿수가 어긋나는 것을 지운다.

    지우기만 하면 fill_missing_shares 가 이웃 값을 이어받아 채우고 시가총액도
    다시 계산한다. 반환값은 지운 분기 목록 — 조용히 고치면 안 되므로 호출부에서
    로그로 남긴다.
    """
    quarters = sort_quarters(record.get("quarters", {}), newest_first=False)
    # 판정을 먼저 다 끝내고 나서 지운다. 지우면서 판정하면 앞 분기를 지운 탓에 뒤
    # 분기의 이웃이 사라져, 정작 틀린 값이 멀쩡한 값으로 통과한다.
    dropped = [q for q in quarters
               if shares_look_wrong(record, q,
                                    record["quarters"][q].get("상장주식수"), limit)]
    for qkey in dropped:
        slot = record["quarters"][qkey]
        slot.pop("상장주식수", None)
        slot.pop("시가총액", None)
        slot.pop("shares_src", None)
    return dropped


def set_shares(record: dict, qkey: str, shares: float) -> bool:
    """
    주식수를 덮어쓰고 시가총액을 다시 계산한다.

    merge_quarter 는 기존 값을 지키지만, 잘못 채워진 값을 바로잡을 때는 덮어써야 한다.
    (수권주식수를 발행주식수로 잘못 읽어 시총이 부풀려진 경우)

    단, 이웃 분기들과 자릿수가 어긋나는 값은 받지 않는다. DART 가 그런 값을
    돌려주기도 하는데, 그대로 쓰면 시가총액이 100만 배가 되어 PBR·PER 이 통째로
    거짓이 된다. 값이 없는 편이 틀린 값보다 낫다.
    """
    slot = record.setdefault("quarters", {}).get(qkey)
    if slot is None or not shares or shares <= 0:
        return False
    if shares_look_wrong(record, qkey, shares):
        return False
    if slot.get("상장주식수") == shares and slot.get("시가총액"):
        return False
    slot["상장주식수"] = shares
    close = slot.get("종가")
    slot["시가총액"] = close * shares if close else None
    return True


def fill_missing_shares(record: dict) -> int:
    """
    주식수가 빈 분기를 가장 가까운 분기 값으로 메우고 시가총액을 다시 계산한다.

    DART 주식총수현황이 분기보고서에는 없는 경우가 있어, 그대로 두면 그 분기의
    PBR·PER 이 통째로 비어버린다. 주식수는 증자·감자가 없는 한 잘 바뀌지 않으므로
    **직전 분기 값을 이어받는 것**이 오늘 주식수를 쓰는 것보다 훨씬 정확하다.
    직전 값이 없는 가장 오래된 구간만 이후 값을 거꾸로 가져온다.
    """
    quarters = sort_quarters(record.get("quarters", {}), newest_first=False)
    filled, last = 0, None
    for qkey in quarters:                       # 과거 -> 현재: 직전 값을 이어받는다
        slot = record["quarters"][qkey]
        known = slot.get("상장주식수")
        if known:
            last = known
        elif last:
            slot["상장주식수"] = last
            slot["shares_src"] = "carried"
            filled += 1
    nxt = None
    for qkey in reversed(quarters):             # 가장 오래된 구간만 거꾸로 메운다
        slot = record["quarters"][qkey]
        known = slot.get("상장주식수")
        if known:
            nxt = known
        elif nxt:
            slot["상장주식수"] = nxt
            slot["shares_src"] = "carried-back"
            filled += 1
    for qkey in quarters:                       # 시가총액 재계산
        slot = record["quarters"][qkey]
        close, shares = slot.get("종가"), slot.get("상장주식수")
        if close and shares:
            slot["시가총액"] = close * shares
    return filled


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
