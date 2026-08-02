# -*- coding: utf-8 -*-
"""
주요 뉴스 — 네이버 금융에서 제목만 긁어온다.

두 갈래다.
  · 주요뉴스   — 그날 증시에서 크게 다뤄진 기사
  · 많이 본 뉴스 — 사람들이 실제로 많이 클릭한 기사

본문은 가져오지 않는다. 제목과 링크, 언론사만 옮기고 읽는 것은 원문에서 한다.
요약을 만들면 그 순간 앱이 판단을 하게 된다.

**파싱 원칙**: class 이름이 아니라 링크 모양으로 찾는다. 네이버가 화면을 고치면
class 는 매번 바뀌지만 기사 링크 주소는 잘 안 바뀐다. 그래도 못 찾으면 그 칸을
비운다 — 엉뚱한 글을 뉴스라고 내보내는 것보다 빈 칸이 낫다.

    python market_news.py            # 받아서 저장
    python market_news.py --dump     # 파싱이 깨졌을 때 원본 구조를 들여다본다
"""
from __future__ import annotations

import os
import re
import sys
import html
import json
import argparse
import datetime as dt

import requests

FEEDS = [
    ("주요뉴스", "https://finance.naver.com/news/mainnews.naver"),
    ("많이 본 뉴스", "https://finance.naver.com/news/news_list.naver?mode=RANK"),
]

LIMIT = 8            # 갈래당 보여줄 기사 수
TIMEOUT = 20
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

# 기사 링크의 모양. 네이버 금융은 둘 중 하나로 건다.
ARTICLE_HREF = re.compile(
    r'(?:news_read\.naver\?[^"\']*article_id=|n\.news\.naver\.com/mnews/article/)',
    re.I)
ANCHOR = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.I | re.S)
HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
TAG = re.compile(r'<[^>]+>')
WS = re.compile(r'\s+')

STORE_PATH = os.path.join("store", "market_news.json")
DOCS_PATH = os.path.join("docs", "market_news.json")


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str) -> str:
    """네이버 금융은 EUC-KR 이다. 응답이 뭐라 하든 실제 바이트로 판단한다."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    raw = r.content
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:2048], re.I)
    enc = (m.group(1).decode("ascii", "ignore") if m else "euc-kr")
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("euc-kr", errors="replace")


def clean(s: str) -> str:
    return WS.sub(" ", html.unescape(TAG.sub(" ", s))).strip()


def absolute(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://finance.naver.com" + href
    return href


def parse(doc: str, limit: int = LIMIT) -> list:
    """
    기사 링크를 순서대로 뽑는다.

    같은 기사가 제목과 썸네일로 두 번 걸리는데, 썸네일 쪽은 <a> 안이 <img> 라
    글자가 없다. 링크 주소로 중복을 지우면서 글자가 있는 쪽을 남긴다.
    """
    out, seen = [], set()
    for attrs, inner in ANCHOR.findall(doc):
        m = HREF.search(attrs)
        if not m or not ARTICLE_HREF.search(m.group(1)):
            continue
        title = clean(inner)
        if len(title) < 8:          # 썸네일·'더보기' 따위
            continue
        url = absolute(html.unescape(m.group(1)))
        key = re.sub(r"[?&](?:mode|type|date|page)=[^&]*", "", url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": url})
        if len(out) >= limit:
            break
    return out


def collect(limit: int = LIMIT) -> dict:
    groups, failed = [], []
    for name, url in FEEDS:
        try:
            items = parse(fetch(url), limit)
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{name} 실패 ({type(e).__name__}: {e})")
            items, failed = [], failed + [name]
        else:
            if not items:
                log(f"::warning::{name}: 기사를 하나도 못 찾았습니다 — "
                    f"네이버 화면 구조가 바뀌었을 수 있습니다 "
                    f"(python market_news.py --dump 로 확인)")
                failed.append(name)
        log(f"  {name}: {len(items)}건")
        groups.append({"name": name, "url": url, "items": items})

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "네이버 금융",
        "groups": groups,
        "failed": failed,
    }


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def dump() -> int:
    """파싱이 깨졌을 때 쓴다. 실제로 뭐가 내려오는지 눈으로 본다."""
    for name, url in FEEDS:
        log(f"\n===== {name} {url} =====")
        try:
            doc = fetch(url)
        except Exception as e:                   # noqa: BLE001
            log(f"  가져오기 실패: {type(e).__name__}: {e}")
            continue
        log(f"  길이 {len(doc)}자")
        hrefs = [HREF.search(a).group(1) for a, _ in ANCHOR.findall(doc)
                 if HREF.search(a)]
        log(f"  <a> {len(hrefs)}개, 기사 링크로 잡힌 것 "
            f"{sum(1 for h in hrefs if ARTICLE_HREF.search(h))}개")
        log("  링크 표본 20개:")
        for h in hrefs[:20]:
            log(f"    {h[:120]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="네이버 금융 뉴스 수집")
    p.add_argument("--limit", type=int, default=LIMIT)
    p.add_argument("--dump", action="store_true",
                   help="파싱하지 않고 받은 문서의 링크 구조를 출력")
    a = p.parse_args(argv)
    if a.dump:
        return dump()

    payload = collect(a.limit)
    save(payload)
    total = sum(len(g["items"]) for g in payload["groups"])
    if not total:
        # 뉴스가 없다고 수집 전체를 실패로 만들지는 않는다. 지수·시장지도는
        # 이미 저장됐고, 뉴스 칸만 비면 된다. 다만 조용히 넘기지도 않는다.
        log("::error::뉴스를 하나도 받지 못했습니다 — 화면의 뉴스 칸이 빕니다")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
