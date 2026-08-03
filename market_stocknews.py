# -*- coding: utf-8 -*-
"""
종목 뉴스 — 목록에 나온 종목마다 최근 기사 몇 건.

상세 화면 아래에 붙는다. 본문은 안 가져온다. 제목·언론사·시각·링크만 옮기고
읽는 것은 원문에서 한다.

**썸네일은 네이버 이미지 주소를 그대로 쓴다.** 우리 저장소로 내려받지 않는다.
남의 이미지를 복사해 두는 것이 되고, 매일 수백 장이면 저장소가 감당이 안 된다.
대신 바깥으로 요청이 나가므로 **오프라인에서는 그림만 빈다** (제목은 캐시에서
그대로 나온다). 주소가 없으면 그림 없이 제목만 나간다.

어디서 받는지 짐작하지 않는다. 두 곳을 차례로 두드려 보고, 무엇이 통했는지와
받아온 것의 모양을 store/ 에 적어 둔다 — 이 저장소에서 짐작으로 긁다가 여러 번
헛돌았다.

  1) m.stock.naver.com 의 종목 뉴스 (JSON, 썸네일 있음)
  2) finance.naver.com 종목 뉴스 목록 (HTML, 썸네일 없음)

    python market_stocknews.py
    python market_stocknews.py --max 20      # 조금만 받아 모양만 본다
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

import market_news                      # decode() · clean() 를 함께 쓴다

API_URL = ("https://m.stock.naver.com/api/news/stock/{code}"
           "?pageSize={n}&page=1&searchMethod=title_entity_id.basic")
HTML_URL = "https://finance.naver.com/item/news_news.naver?code={code}&page=1"

STRONG_PATH = os.path.join("docs", "market_strong.json")
STORE_PATH = os.path.join("store", "market_stocknews.json")
DOCS_PATH = os.path.join("docs", "market_stocknews.json")

PER_STOCK = 4            # 종목당 기사 수
PAUSE = 0.12             # 남의 서버다
TIMEOUT = 15
UA = market_news.UA


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 1) JSON
# =============================================================================

def walk(node, out: list) -> None:
    """
    응답 어디에 기사 목록이 있는지 짐작하지 않는다.

    제목처럼 보이는 칸을 가진 dict 를 전부 주워 담는다. 네이버가 감싸는 모양을
    바꿔도(리스트가 한 겹 더 생기는 식) 그대로 견딘다.
    """
    if isinstance(node, list):
        for x in node:
            walk(x, out)
    elif isinstance(node, dict):
        if node.get("title") and (node.get("articleId") or node.get("aid")):
            out.append(node)
        for v in node.values():
            walk(v, out)


def when(s: str) -> str:
    """'20260803174500' 이나 '2026-08-03 17:45' 을 'MM-DD HH:MM' 으로."""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) >= 12:
        return f"{d[4:6]}-{d[6:8]} {d[8:10]}:{d[10:12]}"
    if len(d) >= 8:
        return f"{d[4:6]}-{d[6:8]}"
    return ""


def from_api(code: str, n: int):
    r = requests.get(API_URL.format(code=code, n=n),
                     headers={"User-Agent": UA, "Referer":
                              f"https://m.stock.naver.com/domestic/stock/{code}/news"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    raw, out = [], []
    walk(r.json(), raw)
    seen = set()
    for a in raw:
        oid = str(a.get("officeId") or a.get("oid") or "")
        aid = str(a.get("articleId") or a.get("aid") or "")
        key = f"{oid}/{aid}"
        if not aid or key in seen:
            continue
        seen.add(key)
        img = a.get("imageOriginLink") or a.get("thumbnail") or ""
        out.append({
            "title": market_news.clean(str(a.get("title") or "")),
            "url": (f"https://n.news.naver.com/mnews/article/{oid}/{aid}"
                    if oid else ""),
            "office": str(a.get("officeName") or a.get("officeId") or ""),
            "at": when(a.get("datetime") or a.get("dt") or ""),
            "image": img if str(img).startswith("http") else "",
        })
        if len(out) >= n:
            break
    return out


# =============================================================================
# 2) HTML (썸네일 없음)
# =============================================================================

ROW = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*(?:news_read\.naver|article)[^"]*)"[^>]*'
    r'(?:title="(?P<t>[^"]*)")?[^>]*>(?P<body>.*?)</a>', re.I | re.S)
OFFICE = re.compile(r'class="info[^"]*"[^>]*>(.*?)<', re.I | re.S)
DATE = re.compile(r'class="date[^"]*"[^>]*>(.*?)<', re.I | re.S)


def from_html(code: str, n: int):
    r = requests.get(HTML_URL.format(code=code),
                     headers={"User-Agent": UA,
                              "Referer": f"https://finance.naver.com/item/main.naver?code={code}"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    doc = market_news.decode(r.content, m.group(1) if m else "")
    if not doc:
        return []
    out, seen = [], set()
    for hit in ROW.finditer(doc):
        title = market_news.clean(hit.group("t") or hit.group("body"))
        href = html.unescape(hit.group("href"))
        if len(title) < 8 or title in seen:
            continue
        seen.add(title)
        out.append({"title": title, "url": market_news.absolute(href),
                    "office": "", "at": "", "image": ""})
        if len(out) >= n:
            break
    return out


# =============================================================================

def for_code(code: str, n: int):
    """(기사 목록, 어디서 받았는지). 둘 다 실패하면 빈 목록."""
    try:
        got = from_api(code, n)
        if got:
            return got, "api"
    except Exception:                            # noqa: BLE001
        pass
    try:
        got = from_html(code, n)
        if got:
            return got, "html"
    except Exception:                            # noqa: BLE001
        pass
    return [], "실패"


def shown_codes(path: str = STRONG_PATH) -> list:
    """목록에 실제로 나온 종목만. 2,500종목에 뉴스를 붙일 이유가 없다."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    lists = [d.get("급상승") or [], d.get("급하락") or []]
    lists += [m.get("strong") or [] for m in (d.get("markets") or {}).values()]
    for lst in lists:
        for r in lst:
            c = r.get("code")
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def collect(limit: int = 0, per: int = PER_STOCK) -> dict:
    codes = shown_codes()
    if not codes:
        raise SystemExit("목록 파일(docs/market_strong.json)이 없습니다 — "
                         "market_strong.py 를 먼저 돌려야 합니다")
    if limit:
        codes = codes[:limit]
    log(f"[수집] 종목 {len(codes)}개 · 종목당 {per}건")

    news, source, imgs = {}, {}, 0
    for code in codes:
        got, where = for_code(code, per)
        source[where] = source.get(where, 0) + 1
        if got:
            news[code] = got
            imgs += sum(1 for a in got if a["image"])
        time.sleep(PAUSE)

    total = sum(len(v) for v in news.values())
    log(f"  기사 {total}건 · 종목 {len(news)}/{len(codes)} · 그림 {imgs}건")
    log("  받은 곳: " + (", ".join(f"{k} {v}" for k, v in source.items()) or "없음"))
    return {"date": dt.date.today().strftime("%Y%m%d"),
            "source": "네이버 금융", "per": per,
            "asked": len(codes), "got": len(news), "articles": total,
            "images": imgs, "by_source": source, "news": news}


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="종목별 뉴스")
    p.add_argument("--max", type=int, default=0, help="종목 수 상한 (0=전부)")
    p.add_argument("--per", type=int, default=PER_STOCK)
    a = p.parse_args(argv)
    payload = collect(a.max, a.per)
    save(payload)
    for code, arts in list(payload["news"].items())[:3]:
        log(f"  {code}: " + " | ".join(
            f"{x['title'][:28]}({x['office']}{' 📷' if x['image'] else ''})"
            for x in arts[:2]))
    return 0 if payload["got"] else 1


if __name__ == "__main__":
    sys.exit(main())
