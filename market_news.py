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
TITLE_ATTR = re.compile(r'title\s*=\s*["\']([^"\']+)["\']', re.I)
TAG = re.compile(r'<[^>]+>')
WS = re.compile(r'\s+')

STORE_PATH = os.path.join("store", "market_news.json")
DOCS_PATH = os.path.join("docs", "market_news.json")


def log(msg: str) -> None:
    print(msg, flush=True)


HANGUL = re.compile(r"[가-힣]")
ALIAS = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "ksc5601": "cp949",
         "euckr": "cp949"}


def decode(raw: bytes, declared: str = "") -> str | None:
    """
    바이트를 글자로. **엄격하게** 풀고, 실패하면 다음 후보로 넘어간다.

    처음엔 선언된 charset 하나로 errors='replace' 를 썼다. 그랬더니 페이지가
    UTF-8 이라고 써 놓고 실제로는 EUC-KR 을 보내는 바람에 한글이 전부 U+FFFD 로
    뭉개진 채 그대로 화면에 나갔다. replace 는 틀린 것을 조용히 지나가게 한다.

    엄격하게 풀면 틀린 코덱은 대부분 그 자리에서 터진다. 그래도 통과할 수 있으니
    마지막에 한글이 실제로 보이는지 확인한다.
    """
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
            text = raw.decode(enc)          # errors 기본값 = strict
        except (UnicodeDecodeError, LookupError):
            continue
        if HANGUL.search(text):
            return text
        # 풀리기는 했는데 한글이 없다. 한국어 페이지에서는 코덱이 틀렸다는 뜻이다.
        fallback = fallback or text
    return fallback


def fetch(url: str):
    """(본문, 진단) — 진단은 무엇이 어떻게 풀렸는지 로그에 남기기 위한 것."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    declared = m.group(1) if m else ""
    text = decode(r.content, declared)
    info = (f"{len(r.content)}바이트 · 헤더 charset={declared or '없음'} · "
            f"한글 {'있음' if text and HANGUL.search(text) else '없음'}")
    return text, info


def clean(s: str) -> str:
    return WS.sub(" ", html.unescape(TAG.sub(" ", s))).strip()


def absolute(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://finance.naver.com" + href
    return href


def article_id(url: str) -> str:
    """중복 판정용 열쇠. 같은 기사인데 mode·date 만 다른 링크가 흔하다."""
    m = re.search(r"article_id=(\d+).*?office_id=(\d+)", url)
    if m:
        return f"{m.group(2)}/{m.group(1)}"
    m = re.search(r"/mnews/article/(\d+)/(\d+)", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return url


def parse(doc: str, limit: int = LIMIT) -> list:
    """
    기사 링크를 순서대로 뽑는다.

    같은 기사가 제목과 썸네일로 두 번 걸리는데, 썸네일 쪽은 <a> 안이 <img> 라
    글자가 없다. 기사 번호로 중복을 지우면서 글자가 있는 쪽을 남긴다.

    제목은 title 속성을 먼저 본다. 목록에 보이는 글자는 네이버가 미리 잘라
    '…' 을 붙여 두는데, title 속성에는 원래 제목이 통째로 들어 있다.
    """
    if not doc:
        return []
    out, seen = [], set()
    for attrs, inner in ANCHOR.findall(doc):
        m = HREF.search(attrs)
        if not m or not ARTICLE_HREF.search(m.group(1)):
            continue
        t = TITLE_ATTR.search(attrs)
        title = clean(t.group(1)) if t else ""
        if len(title) < 8:
            title = clean(inner)
        if len(title) < 8:          # 썸네일·'더보기' 따위
            continue
        url = absolute(html.unescape(m.group(1)))
        key = article_id(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": url, "id": key})
        if len(out) >= limit:
            break
    return out


def collect(limit: int = LIMIT) -> dict:
    groups, failed = [], []
    for name, url in FEEDS:
        info = ""
        try:
            doc, info = fetch(url)
            items = parse(doc, limit)
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{name} 실패 ({type(e).__name__}: {e})")
            items, failed = [], failed + [name]
        else:
            if not items:
                log(f"::warning::{name}: 기사를 하나도 못 찾았습니다 — "
                    f"네이버 화면 구조가 바뀌었을 수 있습니다 "
                    f"(python market_news.py --dump 로 확인)")
                failed.append(name)
        log(f"  {name}: {len(items)}건 ({info})")
        groups.append({"name": name, "url": url, "items": items})

    # 두 갈래가 같은 기사면 한쪽 주소가 안 먹힌 것이다. 화면에는 서로 다른
    # 이름표가 붙는데 내용이 같으면, 없는 것보다 나쁜 거짓말이 된다.
    filled = [g for g in groups if g["items"]]
    if len(filled) > 1:
        ids = [tuple(i["id"] for i in g["items"]) for g in filled]
        if len(set(ids)) == 1:
            log("::warning::갈래들이 같은 기사를 돌려줬습니다 — "
                "한쪽 주소가 무시된 것으로 보입니다. 중복된 갈래는 비웁니다")
            for g in filled[1:]:
                g["items"] = []
                failed.append(g["name"])

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
            doc, info = fetch(url)
        except Exception as e:                   # noqa: BLE001
            log(f"  가져오기 실패: {type(e).__name__}: {e}")
            continue
        if not doc:
            log(f"  글자로 풀지 못했습니다 ({info})")
            continue
        log(f"  {info} · {len(doc)}자")
        t = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
        log(f"  <title>: {clean(t.group(1)) if t else '없음'}")
        hrefs = [HREF.search(a).group(1) for a, _ in ANCHOR.findall(doc)
                 if HREF.search(a)]
        arts = [h for h in hrefs if ARTICLE_HREF.search(h)]
        log(f"  <a> {len(hrefs)}개 · 기사 링크 {len(arts)}개 · "
            f"기사 번호 {len({article_id(h) for h in arts})}개")
        log("  기사 링크 표본 10개:")
        for h in arts[:10]:
            log(f"    {h[:130]}")
        log("  파싱 결과 5건:")
        for it in parse(doc, 5):
            log(f"    {it['id']}  {it['title'][:60]}")
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
    if payload["failed"]:
        # 일부만 실패해도 0 을 돌려주면 워크플로가 초록으로 끝나 아무도 모른다.
        # 0 이 아니면 워크플로가 곧바로 --dump 를 돌려 원인을 로그에 남긴다.
        log(f"::warning::비어 있는 갈래: {', '.join(payload['failed'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
