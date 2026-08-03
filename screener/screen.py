# -*- coding: utf-8 -*-
"""
밸류 조건에 걸린 종목을 추린다.

첫 화면의 "내 인사이트" 자리에 들어갈 목록이다. 새로 수집하는 것은 없고,
이미 모아둔 store/facts 의 최신 분기 지표에 조건을 걸기만 한다.

조건은 아래 RULES 하나에만 적혀 있다. 기준을 바꾸고 싶으면 여기만 고치면 되고,
화면에는 그 조건이 그대로 문장으로 나간다 — 어떤 자로 잰 목록인지 보이지 않으면
숫자를 믿을 근거가 없다.

이 목록은 추천이 아니다. "이 조건에 걸렸다"는 사실만 말한다.
"""
from __future__ import annotations

import os
import json
import datetime as dt

# --- 조건 (여기만 고치면 걸러지는 종목이 바뀐다) -----------------------------
# (지표명, 최소, 최대) — None 은 그쪽 방향으로 제한 없음.
RULES = [
    ("PBR",        None, 1.0),    # 자기자본보다 싸게 거래되는가
    ("PER",        0.0,  10.0),   # 이익 대비 싼가 (적자면 PER 자체가 안 나온다)
    ("ROE(%)",     8.0,  None),   # 그 자기자본으로 벌기는 하는가
    ("부채비율(%)", None, 200.0),  # 빚으로 버티는 중은 아닌가
    ("영업흑자 분기", 4.0, None),   # 최근 분기들이 계속 흑자였는가
]

# 걸린 종목을 어떤 순서로 보여줄지. 낮을수록 앞. 값이 없으면 맨 뒤.
RANK_KEY = "PBR/ROE"

# 화면에 함께 보여줄 숫자들 (순서대로)
SHOW = ["PBR", "PER", "ROE(%)"]

DOCS_PATH = os.path.join("docs", "screen.json")
HISTORY_PATH = os.path.join("store", "screen_history.json")
KEEP_DAYS = 40          # 기록을 남기는 날 수. 첫 화면은 직전 하루만 본다.


def rule_text() -> str:
    """조건을 사람이 읽는 한 줄로. 화면에 그대로 나간다."""
    parts = []
    for key, lo, hi in RULES:
        if lo is not None and hi is not None:
            parts.append(f"{key} {_n(lo)}~{_n(hi)}")
        elif hi is not None:
            parts.append(f"{key} {_n(hi)} 이하")
        else:
            parts.append(f"{key} {_n(lo)} 이상")
    return " · ".join(parts)


def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def latest(metrics: dict, key: str):
    """최신 분기 값. 그 분기에 값이 없으면 None (과거 값으로 대신하지 않는다)."""
    vals = metrics.get(key)
    return vals[0] if vals else None


def passes(metrics: dict) -> bool:
    """
    모든 조건을 만족하는가.

    값이 없으면 탈락시킨다. 모르는 것을 통과시키면 조건을 건 의미가 없다.
    """
    for key, lo, hi in RULES:
        v = latest(metrics, key)
        if v is None:
            return False
        if lo is not None and v < lo:
            return False
        if hi is not None and v > hi:
            return False
    return True


def build(data: dict, as_of: str = "") -> dict:
    """
    site.build 가 만든 {code: {name, metrics, halted, currency, ...}} 에서 추린다.

    외화로 보고하는 종목은 뺀다. 원화 시가총액과 외화 재무제표를 섞어 만든
    PBR·PER 은 숫자만 그럴듯하고 뜻이 없다.
    """
    hits = []
    for code, c in data.items():
        if (c.get("currency") or "KRW").upper() != "KRW":
            continue
        if c.get("halted"):        # 거래정지 의심 종목은 가격 자체를 믿을 수 없다
            continue
        m = c.get("metrics") or {}
        if not passes(m):
            continue
        hits.append({
            "code": code,
            "name": c.get("name") or code,
            "quarter": (c.get("quarters") or [""])[0],
            "rank": latest(m, RANK_KEY),
            "values": [latest(m, k) for k in SHOW],
        })

    # rank 가 없는 종목은 맨 뒤로. (조건은 통과했지만 순위를 매길 값이 없는 경우)
    hits.sort(key=lambda h: (h["rank"] is None, h["rank"] if h["rank"] is not None else 0))
    return {
        "as_of": as_of,
        "rule": rule_text(),
        "rank_key": RANK_KEY,
        "labels": SHOW,
        "screened": len(data),
        "items": hits,
    }


def kst_today() -> str:
    """
    한국 장 기준 날짜.

    워크플로는 UTC 로 도는데, 한국 장이 끝난 뒤에 돌리면 UTC 로는 아직 같은 날
    낮이라 날짜가 하루 어긋날 수 있다. 이 목록은 한국 장 종가로 만든 것이므로
    한국 날짜로 적는다.
    """
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y%m%d")


def record(payload: dict, today: str = "", path: str = HISTORY_PATH) -> dict:
    """
    직전 수집일의 목록과 견주어 무엇이 새로 들어오고 무엇이 빠졌는지 적는다.

    첫 화면에서 '어제와 같은가'에 답하려면 어제 목록이 있어야 하는데, 지표
    파일에는 오늘 것밖에 없다. 그래서 날짜별 목록을 store/ 에 따로 남긴다.

    같은 날 두 번 돌면 그날 것을 덮어쓰고 **그 전날과** 견준다. 아침 실행에서
    들어온 종목이 오후 실행에서 조용히 사라지면 안 된다.

    견줄 기록이 없으면 changed 를 None 으로 둔다. 비어 있는 것과 '바뀐 게 없다'는
    다른 말이다 — 첫 실행에서 '어제와 같음'이라고 적으면 거짓말이 된다.
    """
    today = today or kst_today()
    days = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                days = json.load(f).get("days") or []
        except (ValueError, OSError):
            days = []                  # 깨진 기록은 없는 것과 같이 다룬다
    now = {h["code"]: h["name"] for h in payload.get("items", [])}

    prev = None
    for d in reversed(days):
        if d.get("date") != today:
            prev = d
            break
    if prev:
        was = prev.get("items") or {}
        payload["changed"] = {
            "since": prev.get("date", ""),
            "new": [{"code": c, "name": n} for c, n in now.items() if c not in was],
            "gone": [{"code": c, "name": n} for c, n in was.items() if c not in now],
        }
    else:
        payload["changed"] = None

    days = [d for d in days if d.get("date") != today]
    days.append({"date": today, "items": now})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"days": days[-KEEP_DAYS:]}, f,
                  ensure_ascii=False, separators=(",", ":"))
    return payload


def save(payload: dict, out_dir: str = None) -> str:
    path = os.path.join(out_dir, "screen.json") if out_dir else DOCS_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return path
