# -*- coding: utf-8 -*-
"""
테마 자료를 어디서 받을 수 있는지 확인만 하는 탐침.

수집기가 아니다. **무엇이 응답하고 그 안에 뭐가 들어 있는지**만 적어 둔다.
이 저장소에서 바깥 사이트를 짐작으로 긁다가 여러 번 헛돌았다 (KIND 는 주소가
404 였고, 금통위는 날짜 형식이 달랐고, 뉴스는 엉뚱한 덩어리를 집었다).
그래서 이번에는 **긁기 전에 무엇이 있는지부터 본다.**

결과는 store/theme_probe.json 에 남긴다. 로그가 아니라 파일이라 커밋되고,
나중에 그대로 열어볼 수 있다.

**저작권에 대하여**: 인포스탁은 유료 데이터 회사다. 유료 자료를 그대로
가져다 쓰는 것은 이용약관 문제가 될 수 있다. 여기서는 '무엇이 공개되어
있는지'만 확인하고, 실제로 쓸지는 그 다음에 정한다.

    python theme_probe.py
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

STORE_PATH = os.path.join("store", "theme_probe.json")
TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 확인할 곳들. 되는 것도 안 되는 것도 그대로 적는다.
TARGETS = [
    # 네이버 금융 테마 — 공개 페이지. 테마별 등락률과 편입 종목이 있다.
    ("네이버 테마 목록", "https://finance.naver.com/sise/theme.naver"),
    ("네이버 테마 목록 2쪽", "https://finance.naver.com/sise/theme.naver?&page=2"),
    ("네이버 업종/테마 묶음", "https://finance.naver.com/sise/sise_group.naver?type=theme"),
    # 인포스탁 — 주소 규칙을 모른다. 응답하는지부터 본다.
    ("인포스탁 메인", "http://www.infostock.co.kr/"),
    ("인포스탁 테마(추정)", "http://www.infostock.co.kr/sise/siseD_theme.asp"),
    ("인포스탁데일리", "https://www.infostockdaily.co.kr/"),
]

HANGUL = re.compile(r"[가-힣]")
ALIAS = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "euckr": "cp949"}
THEME_LINK = re.compile(r"(theme|테마)", re.I)


def log(msg: str) -> None:
    print(msg, flush=True)


def decode(raw: bytes, declared: str = "") -> str | None:
    """엄격하게 풀고 한글이 보이는지 확인한다 (market_news.py 와 같은 규칙)."""
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


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def look(name: str, url: str) -> dict:
    out = {"name": name, "url": url}
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                         allow_redirects=True)
    except Exception as e:                       # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["status"] = r.status_code
    out["final_url"] = r.url
    out["bytes"] = len(r.content)
    if r.status_code >= 400:
        return out

    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    doc = decode(r.content, m.group(1) if m else "")
    if not doc:
        out["error"] = "글자로 풀지 못함"
        return out

    t = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
    out["title"] = strip_tags(t.group(1))[:80] if t else ""
    out["hangul"] = bool(HANGUL.search(doc))
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S)
    out["tables"] = len(re.findall(r"<table", doc, re.I))
    out["rows"] = len(rows)

    # 표 앞부분을 그대로 남긴다. 어떤 열이 있는지 봐야 쓸 수 있을지 안다.
    sample = []
    for r_ in rows[:12]:
        cells = [strip_tags(c)[:26] for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r_, re.I | re.S)]
        cells = [c for c in cells if c]
        if cells:
            sample.append(cells[:8])
    out["sample"] = sample[:8]

    # 테마로 들어가는 링크가 있으면 그 모양을 남긴다 — 구성종목을 받을 실마리다.
    links = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', doc, re.I)
    themed = [h for h in links if THEME_LINK.search(h)]
    out["links_total"] = len(links)
    out["theme_links"] = len(themed)
    out["theme_link_sample"] = themed[:6]
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="테마 자료 출처 확인 (수집 아님)")
    p.add_argument("--json", action="store_true", help="결과 JSON 을 그대로 출력")
    a = p.parse_args(argv)

    found = [look(n, u) for n, u in TARGETS]
    payload = {"checked_at": dt.datetime.now(dt.timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
               "targets": found}

    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log(f"[저장] {STORE_PATH}")

    if a.json:
        log(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    for r in found:
        head = f"\n===== {r['name']}  {r['url']}"
        log(head)
        if "error" in r:
            log(f"  실패: {r['error']}")
            continue
        log(f"  {r.get('status')} · {r.get('bytes', 0):,}바이트 · "
            f"한글 {'있음' if r.get('hangul') else '없음'}")
        if r.get("final_url") != r["url"]:
            log(f"  최종 주소: {r.get('final_url')}")
        log(f"  <title>: {r.get('title', '')}")
        log(f"  표 {r.get('tables', 0)}개 · 행 {r.get('rows', 0)}개 · "
            f"테마 링크 {r.get('theme_links', 0)}/{r.get('links_total', 0)}")
        for row in r.get("sample", [])[:6]:
            log(f"    {row}")
        for h in r.get("theme_link_sample", [])[:4]:
            log(f"    링크 {h[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
