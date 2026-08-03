# -*- coding: utf-8 -*-
"""
헤드라인 뉴스 — 지금 가장 많이 쓰인 주제 다섯.

**무엇을 세는가.** 한 매체가 크게 뽑은 제목은 그 매체의 판단이다. 여러 매체가
같은 시간에 같은 사건을 쓰면 그건 시장이 실제로 보고 있는 것이다. 그래서
기사 하나하나가 아니라 **같은 주제로 몇 곳이 썼는지**를 센다. 앱은 판단하지
않는다 — 발행 수는 사실이고, 순위는 그 사실의 정렬일 뿐이다.

**어떻게 묶는가.** 제목에서 뜻을 지닌 낱말만 남기고(조사·흔한 말은 버린다),
낱말이 겹치면 같은 주제로 본다. 형태소 분석기를 쓰지 않는다 — 헤드라인은
고유명사가 그대로 반복되므로 겹침만으로 충분하고, 사전 없이 돌아야 15분마다
가볍게 돌 수 있다.

**무엇을 보여주는가.** 묶음에서 **본문이 가장 긴 기사**. 같은 사건이라도
속보 한 줄과 해설 기사는 읽고 나서 남는 것이 다르다.

묶음이 잘못 잡히면 순위가 통째로 거짓이 된다. 그래서 묶음마다 어떤 낱말로
묶였는지(keywords)와 어떤 제목들이 들어왔는지를 store/ 에 남긴다.

    python market_headline.py           # 받아서 저장
    python market_headline.py --dump    # 묶음이 어떻게 잡혔는지만 출력
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import html
import argparse
import datetime as dt
from urllib.parse import urljoin

import requests

from market_news import (decode, clean, ANCHOR, HREF, TITLE_ATTR, ARTICLE_HREF,
                         MARKER, owner_of)

STORE_PATH = os.path.join("store", "market_headline.json")
DOCS_PATH = os.path.join("docs", "market_headline.json")

TOP = 5              # 화면에 내보낼 순위
BODY_PER_TOPIC = 6   # 묶음마다 본문을 재 볼 기사 수 (전부 재면 한 판이 너무 길어진다)
MIN_TITLE = 10
TIMEOUT = 15
PAUSE = 0.12
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

# 목록을 여러 곳에서 받는다. 한 곳만 보면 그 매체 편집의 그림자가 그대로
# 순위가 된다. 네이버 뉴스는 국내 주요 일간지·경제지 기사를 한자리에 모아
# 두므로, 섹션을 나눠 받으면 발행 수를 셀 만한 표본이 된다.
#
# 세 번째 칸이 **기사 목록을 담은 덩어리** 다. 이름을 못박는다.
#
#   처음엔 '그 쪽에서 가장 큰 덩어리'를 골랐다. 사이드바가 본문 목록보다
#   작을 거라 여겼는데, 실제로는 반대였다.
#
#     덩어리/경제        83건 rl_border · 46건 sa_text
#     덩어리/증권        83건 rl_border · 36건 sa_text
#     덩어리/금융        83건 rl_border · 36건 sa_text
#     덩어리/글로벌 경제   83건 rl_border · 36건 sa_text
#
#   rl_border 는 네 쪽 모두에 똑같이 실린 '많이 본 뉴스' 다. 그게 제일 커서
#   네 쪽 다 같은 83건을 집었고(새것 0건), 표본이 99건으로 쪼그라들면서
#   경제 섹션 순위에 고깃집 기사와 재판 기사가 올라왔다.
#
#   크기로는 못 가른다. 이름으로 가른다. 이름이 바뀌면 그 갈래는 빈다 —
#   엉뚱한 목록을 헤드라인이라고 내보내는 것보다 낫다 (market_news 와 같은 원칙).
FEEDS = [
    ("경제", "https://news.naver.com/section/101", "sa_text"),
    ("증권", "https://news.naver.com/breakingnews/section/101/258", "sa_text"),
    ("금융", "https://news.naver.com/breakingnews/section/101/259", "sa_text"),
    ("글로벌 경제", "https://news.naver.com/breakingnews/section/101/262", "sa_text"),
    ("금융 주요뉴스", "https://finance.naver.com/news/mainnews.naver", "newsList"),
]

READ_URL = "https://n.news.naver.com/mnews/article/{oid}/{aid}"

# 본문 자리. 네이버 기사 화면은 이 셋 중 하나에 본문을 담는다.
BODY_BOX = [
    re.compile(r'<article[^>]*id=["\']dic_area["\'][^>]*>(.*?)</article>', re.I | re.S),
    re.compile(r'<div[^>]*id=["\']dic_area["\'][^>]*>(.*?)</div>', re.I | re.S),
    re.compile(r'<div[^>]*id=["\']newsct_article["\'][^>]*>(.*?)</div>', re.I | re.S),
]
OFFICE = [
    re.compile(r'<meta\s+property=["\']og:article:author["\']\s+content=["\']([^"\']+)',
               re.I),
    re.compile(r'class=["\'][^"\']*media_end_head_top_logo_img[^"\']*["\'][^>]*'
               r'title=["\']([^"\']+)', re.I),
    re.compile(r'"officeName"\s*:\s*"([^"]+)"'),
]
AT = re.compile(r'data-date-time=["\']([\d\-: ]+)', re.I)
# 언론사 이름 뒤에 '| 네이버' 가 붙어 온다. 기사를 쓴 곳은 네이버가 아니다.
OFFICE_TAIL = re.compile(r"\s*[|·\-–]\s*(?:네이버(?:\s*뉴스)?|Naver).*$", re.I)

# 제목으로 받아들이지 않을 것.
#
#   첫 실행에서 1위가 이것이었다:  '" style="display: none;">   (125건)
#   네이버 화면 안의 자바스크립트가 문자열로 들고 있는 HTML 조각을 <a> 로
#   읽은 것이다. 조각들끼리는 '같은 제목'이라 한 묶음이 되어 125건짜리
#   1위가 됐다. 묶기 전에 **제목이 제목인지** 부터 본다.
BAD_TITLE = re.compile(r"[<>]|style\s*=|display\s*:|function\s*\(|\{\{|\}\}|&#")
HANGUL = re.compile(r"[가-힣]")
MIN_HANGUL = 4          # 한국어 기사 제목이면 이만큼은 있다

# 조사와 흔한 말. 이것들이 겹친다고 같은 주제가 아니다.
STOP = set("""
그리고 그러나 하지만 대한 위해 통해 관련 밝혔다 말했다 전했다 나타났다 오늘
내일 어제 올해 지난해 지난달 이번 최근 오전 오후 기자 뉴스 속보 단독 종합
있다 없다 한다 됐다 이다 이어 다시 아직 대해 대비 기준 전망 예상 분석 발표
가능 확대 추진 계획 검토 방침 강조 요구 지적 언급 논의 결정 발언 상황 문제
사업 시장 경제 산업 기업 정부 국내 해외 사상 최대 최고 최저 역대 상승 하락
급등 급락 마감 개장 출발 기록 돌파 하루 이틀 사흘 그것 이것 무엇 누구
""".split())

# 숫자만 있거나 한 글자인 토큰은 버린다. '3%' 는 주제가 아니다.
TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
# 붙어 있는 조사. 세 글자 이상일 때만 떼어 낸다 ('금리' 에서 '리' 를 떼면 안 된다).
JOSA = ("으로", "에서", "에게", "까지", "부터", "이나", "라며", "라고", "면서",
        "은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "과", "와",
        "만", "및")


def log(msg) -> None:
    print(msg, flush=True)


def norm(tok: str) -> str:
    """
    붙은 조사를 뗀다. '코스피가' 와 '코스피는' 은 같은 낱말이다.

    **세 글자 낱말은 건드리지 않는다.** 두 글자만 남기면 낱말이 아닌 것이
    나온다 — '순매도' 에서 '도' 를 떼면 '순매' 가 되어 '순매수' 와 갈라지고,
    엉뚱한 제목끼리 '순매' 로 묶인다. 떼고 나서 세 글자 이상 남을 때만 뗀다.
    """
    for j in JOSA:
        if len(tok) - len(j) >= 3 and tok.endswith(j):
            return tok[: -len(j)]
    return tok


def tokens(title: str) -> set:
    out = set()
    for t in TOKEN.findall(title):
        t = norm(t)
        if len(t) < 2 or t in STOP:
            continue
        out.add(t)
    return out


def same_topic(seed: set, other: set) -> bool:
    """
    같은 주제인가.

    낱말 두 개 이상이 겹치고, 겹친 비율도 어느 정도 되어야 한다. 겹침 개수만
    보면 긴 제목끼리 우연히 두 개가 겹쳐 엮이고, 비율만 보면 짧은 제목 둘이
    한 낱말로 엮인다. 둘 다 건다.
    """
    if not seed or not other:
        return False
    both = seed & other
    if len(both) < 2:
        return False
    return len(both) / len(seed | other) >= 0.22


def linked(a: set, b: set) -> int:
    """
    두 낱말 뭉치가 몇 군데서 만나는가.

    **띄어쓰기만 다른 것을 같은 낱말로 본다.** 실제로 이런 일이 있었다.

        1위  중동 긴장완화 기대에 상승 마감      (4곳)
        3위  중동 긴장 완화 기대에 상승 마감      (4곳)
        5위  미-이란 대화 재개에 다우 사상 최고   (3곳)

    같은 사건인데 '긴장완화' 와 '긴장 완화' 가 다른 낱말로 쪼개져 셋으로
    갈렸다. 다섯 자리 중 셋을 한 사건이 차지하면 '오늘 주목받는 다섯 가지'가
    아니게 된다. 한쪽이 다른 쪽을 품고 있으면 만난 것으로 센다.
    """
    hit = 0
    for x in a:
        for y in b:
            if x == y or (len(x) >= 2 and len(y) >= 2 and (x in y or y in x)):
                hit += 1
                break
    return hit


def core(items: list) -> set:
    """
    묶음의 중심 낱말 — **두 건 이상에 나온 것.**

    한 건에만 있는 낱말은 그 기사의 곁가지다 ('동상이몽', '월가월부').
    묶음끼리 견줄 때 그런 것까지 세면 우연히 스친 것이 만난 것이 된다.
    """
    cnt = {}
    for it in items:
        for t in tokens(it["title"]):
            cnt[t] = cnt.get(t, 0) + 1
    return {t for t, c in cnt.items() if c >= 2}


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    return decode(r.content, m.group(1) if m else "") or ""


def article_key(url: str) -> str:
    """oid/aid. 같은 기사가 목록마다 다른 주소로 걸린다."""
    m = re.search(r"/article/(\d+)/(\d+)", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"office_id=(\d+).*?article_id=(\d+)", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"article_id=(\d+).*?office_id=(\d+)", url)
    if m:
        return f"{m.group(2)}/{m.group(1)}"
    return url


def ok_title(t: str) -> bool:
    """제목처럼 생겼는가. 아니면 묶기 전에 버린다."""
    return (len(t) >= MIN_TITLE and not BAD_TITLE.search(t)
            and len(HANGUL.findall(t)) >= MIN_HANGUL)


def parse_list(doc: str, base: str, block: str = "", why: dict = None,
               name: str = "") -> list:
    """
    목록에서 기사 제목과 주소를, **이름을 지정한 덩어리 안에서만** 집는다.

    한 쪽에는 본문 목록 말고 '많이 본 뉴스' 같은 곁목록이 함께 온다. 그리고
    그 곁목록이 본문 목록보다 클 수 있다 — 네이버 경제 섹션이 그렇다
    (rl_border 83건 vs sa_text 46건). 크기로는 못 가른다.

    덩어리 이름을 못 찾으면 **빈 목록을 낸다.** 엉뚱한 목록을 헤드라인이라고
    내보내는 것보다 낫다. 어느 덩어리에 몇 건이 있었는지는 진단에 남으므로,
    이름이 바뀌면 그걸 보고 고친다.
    """
    doc = doc or ""
    marks = [(m.start(), m.group(2)) for m in MARKER.finditer(doc)]
    hits = []
    for a in ANCHOR.finditer(doc):
        m = HREF.search(a.group(1))
        if not m:
            continue
        href = html.unescape(m.group(2))
        if not ARTICLE_HREF.search(href) and "/article/" not in href:
            continue
        t = TITLE_ATTR.search(a.group(1))
        title = clean(t.group(2)) if t else ""
        if not ok_title(title):
            title = clean(a.group(2))
        if not ok_title(title):
            continue
        url = urljoin(base, href)
        key = article_key(url)
        if "/" not in key:
            continue
        hits.append({"key": key, "title": title, "url": url,
                     "block": owner_of(a.start(), marks)})

    if not hits:
        return []
    counts = {}
    for h in hits:
        counts[h["block"]] = counts.get(h["block"], 0) + 1
    if why is not None and name:
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
        why[f"덩어리/{name}"] = " · ".join(f"{v}건 {k[:28]}" for k, v in top)

    out, seen = [], set()
    for h in hits:
        # 덩어리 이름은 'sa_text' 처럼 딱 떨어지기도 하고 'sa_text foo' 처럼
        # 다른 이름이 붙어 오기도 한다. 들어 있으면 같은 덩어리로 본다.
        if block and block not in h["block"]:
            continue
        if h["key"] in seen:
            continue
        seen.add(h["key"])
        out.append({k: h[k] for k in ("key", "title", "url")})
    if not out and why is not None and name:
        why[f"목록/{name}"] = (f"'{block}' 덩어리를 못 찾아 이 갈래는 비웁니다 "
                              f"— 위 '덩어리/{name}' 을 보고 고칩니다")
    return out


def gather(why: dict) -> list:
    """목록을 모두 받아 기사 번호로 중복을 지운다."""
    pool, seen = [], set()
    for name, url, block in FEEDS:
        try:
            doc = fetch(url)
            items = parse_list(doc, url, block, why, name)
        except Exception as e:                           # noqa: BLE001
            why[f"목록/{name}"] = f"{type(e).__name__}: {e}"[:140]
            log(f"  {name}: 실패 ({type(e).__name__})")
            continue
        fresh = 0
        for it in items:
            if it["key"] in seen:
                continue
            seen.add(it["key"])
            pool.append(it)
            fresh += 1
        if items:
            why[f"목록/{name}"] = f"{len(items)}건 중 새것 {fresh}건"
        log(f"  {name}: {len(items)}건 (새것 {fresh})")
        time.sleep(PAUSE)
    return pool


def cluster(pool: list) -> list:
    """
    제목 낱말이 겹치는 것끼리 묶는다.

    비교 대상은 **묶음의 씨앗**이지 묶음 전체가 아니다. 전체와 비교하면
    낱말이 붙을수록 그물이 커져서 나중엔 아무거나 걸린다 (한 묶음이 40건이
    되면 그건 주제가 아니라 그냥 뉴스다).
    """
    groups = []
    for it in pool:
        tok = tokens(it["title"])
        if len(tok) < 2:
            continue
        for g in groups:
            if same_topic(g["seed"], tok):
                g["items"].append(it)
                break
        else:
            groups.append({"seed": tok, "items": [it]})

    # 두 번째 판 — **묶음끼리 다시 본다.**
    #
    # 첫 판은 씨앗과만 견주므로 그물이 안 커지는 대신, 같은 사건이 씨앗의
    # 말투에 따라 둘셋으로 갈린다. 여기서는 갈린 것을 도로 붙인다. 견주는
    # 것은 씨앗이 아니라 묶음의 중심 낱말이라, 첫 판처럼 끝없이 커지지 않는다.
    merged = []
    for g in groups:
        g["core"] = core(g["items"]) or g["seed"]
        for m in merged:
            if linked(m["core"], g["core"]) >= 2:
                m["items"].extend(g["items"])
                m["core"] = core(m["items"]) or m["core"]
                break
        else:
            merged.append(g)

    merged.sort(key=lambda g: -len(g["items"]))
    return merged


def read_article(key: str, why: dict = None) -> dict:
    """본문 글자수·언론사·시각. 못 읽으면 빈 dict."""
    oid, aid = key.split("/", 1)
    try:
        doc = fetch(READ_URL.format(oid=oid, aid=aid))
    except Exception as e:                               # noqa: BLE001
        if why is not None:
            why.setdefault("본문", f"{type(e).__name__}: {e}"[:120])
        return {}
    body = ""
    for pat in BODY_BOX:
        m = pat.search(doc)
        if m:
            body = clean(m.group(1))
            break
    office = ""
    for pat in OFFICE:
        m = pat.search(doc)
        if m:
            # og:article:author 는 '주간동아 | 네이버' 처럼 온다. 기사를 쓴
            # 곳은 네이버가 아니다 — 뒤에 붙는 것을 뗀다.
            office = OFFICE_TAIL.sub("", html.unescape(m.group(1))).strip()
            break
    at = AT.search(doc)
    return {"chars": len(body), "office": office,
            "at": at.group(1).strip() if at else ""}


def build(pool: list, top: int = TOP, why: dict = None) -> list:
    """
    묶음을 순위대로. 묶음마다 **본문이 가장 긴 기사**를 대표로 세운다.

    본문은 상위 묶음만 잰다. 전체를 재면 한 판이 몇 분씩 걸린다 — 순위에 못 든
    묶음의 본문 길이는 화면에 쓰이지 않는다.
    """
    groups = cluster(pool)
    out = []
    for g in groups[:top]:
        cands = g["items"][:BODY_PER_TOPIC]
        best, offices = None, []
        for it in cands:
            info = read_article(it["key"], why)
            time.sleep(PAUSE)
            if not info:
                continue
            if info.get("office"):
                offices.append(info["office"])
            row = dict(it, **info)
            if best is None or row["chars"] > best["chars"]:
                best = row
        # 본문을 하나도 못 읽었으면 제목만이라도 첫 기사로 세운다. 순위 자체는
        # 제목만으로 셌으므로 여전히 사실이다.
        if best is None:
            best = dict(g["items"][0], chars=0, office="", at="")
        out.append({
            "n": len(g["items"]),
            "title": best["title"], "url": best["url"],
            "office": best.get("office", ""), "at": best.get("at", ""),
            "chars": best.get("chars", 0),
            "offices": sorted(set(o for o in offices if o)),
            "keywords": sorted(g["seed"])[:6],
            "titles": [i["title"] for i in g["items"][:8]],
        })
    return out


def collect(top: int = TOP) -> dict:
    why = {}
    pool = gather(why)
    rank = build(pool, top, why) if pool else []
    why["표본"] = f"{len(pool)}건"
    return {"fetched_at": dt.datetime.now(dt.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "네이버 뉴스 경제·증권·금융",
            "pool": len(pool), "rank": rank, "why": why}


def rank_key(rank: list) -> list:
    """순위가 실제로 바뀌었는지 볼 때 쓰는 열쇠. 시각은 빼고 내용만 본다."""
    return [[r.get("n"), r.get("title"), r.get("url")] for r in rank or []]


def unchanged(rank: list, path: str = DOCS_PATH) -> bool:
    """
    지난번과 순위가 같은가.

    15분마다 도는데 매번 파일을 새로 쓰면 fetched_at 하나 때문에 늘 달라지고,
    순위가 안 바뀐 날에도 하루 96번 커밋·배포가 된다. **순위가 그대로면
    아무것도 쓰지 않는다.** 그러면 화면에 적히는 집계 시각도 '마지막으로
    순위가 바뀐 때'가 되어, 방금 받았지만 어제와 같은 순위인 것보다 오히려
    사실에 가깝다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return False
    return rank_key(old.get("rank")) == rank_key(rank)


def save(payload: dict) -> None:
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    # 화면에는 묶인 제목 목록(titles)과 진단을 보내지 않는다. 자주 받는
    # 파일이라 가벼워야 하고, 묶음 검산은 store/ 에서 한다.
    slim = {k: v for k, v in payload.items() if k != "why"}
    slim["rank"] = [{k: v for k, v in r.items() if k != "titles"}
                    for r in payload["rank"]]
    os.makedirs(os.path.dirname(DOCS_PATH) or ".", exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (진단 포함), {DOCS_PATH}")


def dump() -> int:
    why = {}
    pool = gather(why)
    log(f"\n표본 {len(pool)}건")
    for i, g in enumerate(cluster(pool)[:12], 1):
        log(f"\n{i}. {len(g['items'])}건  낱말: {' '.join(sorted(g['seed'])[:8])}")
        for it in g["items"][:5]:
            log(f"     {it['title'][:70]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="헤드라인 뉴스 — 주제별 발행 수 순위")
    p.add_argument("--top", type=int, default=TOP)
    p.add_argument("--dump", action="store_true")
    a = p.parse_args(argv)
    if a.dump:
        return dump()

    payload = collect(a.top)
    for i, r in enumerate(payload["rank"], 1):
        log(f"  {i}. {r['n']}건 · {r['chars']}자 · {r['title'][:60]}")
    if not payload["rank"]:
        log("::error::헤드라인을 하나도 만들지 못했습니다 — 그 칸이 빕니다")
        return 1
    if unchanged(payload["rank"]):
        log("순위 그대로 — 파일을 다시 쓰지 않습니다 (커밋·배포도 없습니다)")
        return 0
    save(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
