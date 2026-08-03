# -*- coding: utf-8 -*-
"""
테마 — 네이버 금융의 테마 분류를 받아 우리 18개 대분류에 붙인다.

인포스탁은 403 으로 막혀 있고 유료 자료다. 네이버 금융의 테마 페이지는 공개돼
있고 267개 테마와 편입 종목을 준다. 이름 짓는 방식이 인포스탁과 비슷해 보이지만
그건 인상일 뿐이고, 여기서 쓰는 것은 네이버가 공개한 것뿐이다.

**구조는 확인하고 시작했다** (theme_probe.py, 2026-08-03):

    sise_group.naver?type=theme   표 2개 · 435행 · 테마 링크 267개
      열: 테마명 · 전일대비 · 전일대비 등락현황(전체/상승/보합/하락) · 등락그래프
      각 행에 /sise/sise_group_detail.naver?type=theme&no=505

편입 종목은 테마마다 한 번씩 더 받아야 한다. 267개를 다 받으면 남의 서버를
그만큼 두드리는 것이라, **우리 소분류에 걸린 테마만** 받는다.

    python market_theme.py            # 수집·저장
    python market_theme.py --dry      # 목록만 받아 매칭 결과만 본다 (상세 안 받음)
"""
from __future__ import annotations

import os
import re
import sys
import html
import json
import time
import argparse
import datetime as dt

import requests

import market_etf                      # 테마 정의(themes.json)를 함께 쓴다

LIST_URL = "https://finance.naver.com/sise/sise_group.naver?type=theme"
DETAIL_URL = ("https://finance.naver.com/sise/sise_group_detail.naver"
              "?type=theme&no={no}")

STORE_PATH = os.path.join("store", "market_theme.json")
DOCS_PATH = os.path.join("docs", "market_theme.json")

TIMEOUT = 20
PAUSE = 0.15                # 상세를 이어서 받을 때 쉬는 시간 (남의 서버다)
MAX_DETAIL = 140            # 상세를 받을 테마 수 상한
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

HANGUL = re.compile(r"[가-힣]")
ALIAS = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "euckr": "cp949"}
THEME_ROW = re.compile(
    r'<a[^>]+href="[^"]*sise_group_detail\.naver\?type=theme&(?:amp;)?no=(\d+)"[^>]*>'
    r'(.*?)</a>', re.I | re.S)
# 종목 링크. .naver 와 옛 .nhn 을 둘 다 받고, 뒤에 인자가 더 붙어도 된다.
ITEM_ANCHOR = re.compile(
    r'<a\b[^>]*href\s*=\s*(["\'])([^"\']*?/item/main\.n(?:aver|hn)\?code=(\d{6})[^"\']*)\1'
    r'[^>]*>(.*?)</a>', re.I | re.S)
MARKER = re.compile(r'<(?:h[2-5]|div|ul|dl|table|section|tbody)\b[^>]*?'
                    r'(?:class|id)\s*=\s*(["\'])(.*?)\1', re.I)
PCT = re.compile(r'([+\-]?\d+\.\d+)\s*%')


def log(msg: str) -> None:
    print(msg, flush=True)


def decode(raw: bytes, declared: str = "") -> str | None:
    cands = []
    if declared:
        cands.append(declared)
    m = re.search(rb'charset=["\']?([\w.-]+)', raw[:4096], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    cands += ["cp949", "utf-8"]
    fallback = None
    for enc in cands:
        enc = ALIAS.get(enc.strip().lower(), enc)
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if HANGUL.search(text):
            return text
        fallback = fallback or text
    return fallback


def fetch(url: str) -> str | None:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    return decode(r.content, m.group(1) if m else "")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# =============================================================================
# 목록
# =============================================================================

def parse_list(doc: str) -> list:
    """
    테마 한 줄 = 테마 링크 + 그 뒤에 이어지는 숫자들.

    행을 <tr> 로 자르지 않고 링크 위치를 기준으로 자른다. 네이버가 표 구조를
    바꿔도 링크와 그 뒤 숫자의 순서는 잘 안 바뀐다.
    열 순서: 전일대비(%) · 전체 · 상승 · 보합 · 하락
    """
    out = []
    hits = list(THEME_ROW.finditer(doc))
    for i, m in enumerate(hits):
        name = clean(m.group(2))
        if not name:
            continue
        tail = doc[m.end(): hits[i + 1].start() if i + 1 < len(hits) else m.end() + 900]
        text = clean(tail)
        pct = PCT.search(text)
        nums = [int(x) for x in re.findall(r"(?<![\d.])(\d{1,4})(?![\d.%])", text)][:4]
        out.append({
            "no": int(m.group(1)),
            "name": name,
            "chg": float(pct.group(1)) if pct else None,
            "n": nums[0] if len(nums) > 0 else None,
            "up": nums[1] if len(nums) > 1 else None,
            "flat": nums[2] if len(nums) > 2 else None,
            "down": nums[3] if len(nums) > 3 else None,
        })
    # 같은 테마가 목록·요약에 두 번 걸릴 수 있다. 먼저 나온 것을 남긴다.
    seen, uniq = set(), []
    for t in out:
        if t["no"] in seen:
            continue
        seen.add(t["no"])
        uniq.append(t)
    return uniq


def owner_of(pos: int, marks: list) -> str:
    """이 위치보다 앞에 있는 가장 가까운 표지(class/id)."""
    out = "?"
    for mp, mv in marks:
        if mp < pos:
            out = mv
        else:
            break
    return out


def parse_members(doc: str):
    """
    편입 종목 — (종목 목록, 어느 덩어리에서 집었는지).

    **문서에 있는 종목 링크를 전부 집지 않는다.** 네이버 금융 페이지에는
    '인기 검색 종목', '내가 본 종목' 같은 사이드바가 딸려 있고, 거기에도
    같은 모양의 종목 링크가 들어 있다. 뉴스에서 똑같이 당했다 — 링크 모양만
    보고 집었다가 두 페이지가 공유하는 사이드바를 주요뉴스로 내보냈다.

    편입 종목표는 그 페이지에서 종목 링크가 가장 많이 모인 덩어리다.
    사이드바는 대여섯 개뿐이라 규모가 다르다. class 이름을 짐작해 박아 두는
    대신 **가장 큰 덩어리 하나만** 취하고, 어느 표지였는지 함께 돌려준다.
    """
    marks = [(m.start(), m.group(2)) for m in MARKER.finditer(doc)]
    groups = {}
    for m in ITEM_ANCHOR.finditer(doc):
        name = clean(m.group(4))
        if not name:                       # 썸네일처럼 글자가 없는 링크
            continue
        groups.setdefault(owner_of(m.start(), marks), []).append(
            {"code": m.group(3), "name": name})
    if not groups:
        return [], ""
    marker, rows = max(groups.items(), key=lambda kv: len(kv[1]))
    out, seen = [], set()
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append(r)
    return out, marker


# =============================================================================
# 우리 분류에 붙이기
# =============================================================================

def match_theme(name: str, themes: list):
    """
    테마 이름을 우리 소분류의 match 조각과 맞춰본다.

    ETF 이름에 쓰던 조각을 그대로 쓴다. 가장 긴 조각이 이긴다 — 긴 조각일수록
    좁은 뜻이다. 안 걸리면 None 이고, 그런 테마는 '미분류'로 남긴다.
    **감추지 않는다.** 미분류가 뭔지 보여야 조각을 고칠 수 있다.
    """
    low = name.lower()
    best, best_len = None, 0
    for th in themes:
        for sub in th["subs"]:
            for frag in sub.get("match", []):
                if frag and frag.lower() in low and len(frag) > best_len:
                    best, best_len = (th["name"], sub["name"]), len(frag)
    return best


def build(rows: list, themes: list, members: dict) -> dict:
    groups = {}
    for th in themes:
        groups[th["name"]] = {"name": th["name"], "subs": {
            s["name"]: {"name": s["name"], "themes": [], "codes": set()}
            for s in th["subs"]}}
    unmatched = []

    for t in rows:
        hit = match_theme(t["name"], themes)
        t["group"], t["sub"] = (hit if hit else (None, None))
        if not hit:
            unmatched.append(t["name"])
            continue
        slot = groups[hit[0]]["subs"][hit[1]]
        slot["themes"].append({"no": t["no"], "name": t["name"],
                               "chg": t["chg"], "n": t["n"]})
        slot["codes"].update(m["code"] for m in members.get(t["no"], []))

    out = []
    for th in themes:
        g = groups[th["name"]]
        subs, codes, wsum, wn = [], set(), 0.0, 0
        for s in th["subs"]:
            slot = g["subs"][s["name"]]
            codes |= slot["codes"]
            for x in slot["themes"]:
                if x["chg"] is not None and x["n"]:
                    wsum += x["chg"] * x["n"]
                    wn += x["n"]
            subs.append({"name": s["name"],
                         "themes": slot["themes"],
                         "stocks": len(slot["codes"])})
        out.append({"name": th["name"], "subs": subs,
                    "stocks": len(codes),
                    "themes": sum(len(s["themes"]) for s in subs),
                    # 테마마다 종목 수가 다르다. 종목 수로 눌러 평균을 낸다.
                    "chg": round(wsum / wn, 2) if wn else None})
    return {"groups": out, "unmatched": sorted(unmatched)}


# =============================================================================

def collect(max_detail: int = MAX_DETAIL, dry: bool = False) -> dict:
    doc = fetch(LIST_URL)
    if not doc:
        raise SystemExit("테마 목록을 글자로 풀지 못했습니다")
    rows = parse_list(doc)
    if not rows:
        raise SystemExit("테마 목록에서 테마를 찾지 못했습니다 — 구조가 바뀐 듯합니다")
    log(f"[수집] 테마 {len(rows)}개")

    themes = market_etf.load_themes()
    # 우리 소분류에 걸린 테마만 상세를 받는다. 267개를 다 받으면 남의 서버를
    # 그만큼 두드리는 셈이고, 안 걸린 테마의 종목은 어차피 쓸 데가 없다.
    wanted = [t for t in rows if match_theme(t["name"], themes)]
    log(f"  우리 분류에 걸린 테마 {len(wanted)}개 / 미분류 {len(rows) - len(wanted)}개")

    members, failed, markers = {}, [], {}
    if not dry:
        for t in wanted[:max_detail]:
            try:
                d = fetch(DETAIL_URL.format(no=t["no"]))
                got, marker = parse_members(d) if d else ([], "")
            except Exception as e:                # noqa: BLE001
                log(f"  상세 실패 {t['name']} ({type(e).__name__})")
                failed.append(t["name"])
                continue
            if not got:
                failed.append(t["name"])
            markers[marker] = markers.get(marker, 0) + 1
            members[t["no"]] = got
            time.sleep(PAUSE)
        log(f"  편입 종목: {sum(len(v) for v in members.values())}건 "
            f"({len(members)}개 테마, 실패 {len(failed)})")
        # 어느 덩어리에서 집었는지. 한 표지로 몰리지 않으면 구조가 바뀐 것이다.
        for k, v in sorted(markers.items(), key=lambda kv: -kv[1])[:4]:
            log(f"    덩어리 {k or '(없음)'} — {v}개 테마")

    built = build(rows, themes, members)
    return {
        "date": dt.date.today().strftime("%Y%m%d"),
        "source": "네이버 금융 테마",
        "themes_total": len(rows),
        "detail_fetched": len(members),
        "detail_failed": failed,
        "markers": markers,
        "groups": built["groups"],
        "unmatched": built["unmatched"],
        "rows": rows,
    }


def slim_of(payload: dict) -> dict:
    """
    화면이 쓸 것만 남긴다.

    미분류는 **감추지 않는다.** 다만 목록 전체는 store/ 에 두고 화면에는
    몇 개인지만 넘긴다 — 분류가 얼마나 덜 됐는지는 보여야 한다.
    """
    slim = {k: v for k, v in payload.items()
            if k not in ("rows", "markers", "detail_failed", "unmatched")}
    slim["unmatched"] = len(payload.get("unmatched", []))
    return slim


def save(payload: dict) -> None:
    """docs/ 에는 화면이 쓸 것만. rows 전체와 미분류 목록은 store/ 에만 둔다."""
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    slim = slim_of(payload)
    os.makedirs(os.path.dirname(DOCS_PATH) or ".", exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (전체), {DOCS_PATH} (화면용)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="네이버 금융 테마 수집")
    p.add_argument("--dry", action="store_true",
                   help="목록만 받아 매칭 결과만 본다 (상세는 안 받음)")
    p.add_argument("--max", type=int, default=MAX_DETAIL)
    a = p.parse_args(argv)

    payload = collect(a.max, a.dry)
    if not a.dry:
        save(payload)

    log(f"\n{payload['date']} · 테마 {payload['themes_total']}개 · "
        f"상세 {payload['detail_fetched']}개")
    for g in payload["groups"]:
        if not g["themes"]:
            continue
        chg = f"{g['chg']:+.2f}%" if g["chg"] is not None else "  –  "
        log(f"  {g['name']:14} {chg}  테마 {g['themes']:2}개 · 종목 {g['stocks']:3}개")
        for s in g["subs"]:
            if s["themes"]:
                log(f"      {s['name']:14} {s['stocks']:3}종목  "
                    + ", ".join(x["name"][:18] for x in s["themes"][:3]))
    log(f"\n미분류 {len(payload['unmatched'])}개 (앞 25개):")
    for n in payload["unmatched"][:25]:
        log(f"    {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
