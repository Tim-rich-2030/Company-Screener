# -*- coding: utf-8 -*-
"""
관리종목·투자주의환기종목 목록.

pykrx 에는 이 둘을 주는 함수가 없다 (전종목 목록·시세·업종·공매도는 있는데
지정 상태는 없다). 그래서 직접 받아온다.

  · 관리종목        — 네이버 금융 관리종목 페이지 (코스피·코스닥 함께)
  · 투자주의환기종목 — KRX KIND (코스닥 전용 제도)

**못 받으면 빈 집합을 돌려준다.** 그러면 아무것도 제외되지 않는다.
멀쩡한 종목을 잘못 빼는 것보다, 빼야 할 것이 남아 있는 편이 낫다 — 남아 있는
것은 화면에서 보이지만, 잘못 빠진 것은 아무도 눈치채지 못한다. 대신 몇 종목을
받았는지 결과에 적어 두고, 0 이면 로그를 시끄럽게 남긴다.

    python market_flags.py            # 받아서 개수만 출력
    python market_flags.py --dump     # 파싱이 깨졌을 때 원본 구조 확인
"""
from __future__ import annotations

import re
import sys
import html
import argparse

import requests

ADMIN_URL = "https://finance.naver.com/sise/management.naver"
# KIND 투자주의환기종목. 조회 조건 없이 열면 전체 목록이 나온다.
ALERT_URL = ("https://kind.krx.co.kr/investwarn/alertissue.do"
             "?method=searchAlertIssueSub&currentPageSize=500&pageIndex=1")

TIMEOUT = 20
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

# 네이버는 /item/main.naver?code=005930 로, KIND 는 종목코드를 그대로 쓴다.
NAVER_CODE = re.compile(r'/item/main\.naver\?code=(\d{6})')
KIND_CODE = re.compile(r'\b(\d{6})\b')
HANGUL = re.compile(r"[가-힣]")
ALIAS = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "euckr": "cp949"}


def log(msg: str) -> None:
    print(msg, flush=True)


def decode(raw: bytes, declared: str = "") -> str | None:
    """엄격하게 풀고 한글이 보이는지 확인한다. market_news.py 와 같은 규칙."""
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


def admin_issues() -> set:
    """관리종목 종목코드. 네이버 관리종목 표의 종목 링크에서 뽑는다."""
    try:
        doc = fetch(ADMIN_URL)
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::관리종목 가져오기 실패 ({type(e).__name__}: {e})")
        return set()
    if not doc:
        log("::warning::관리종목 페이지를 글자로 풀지 못했습니다")
        return set()
    return set(NAVER_CODE.findall(doc))


def alert_issues() -> set:
    """
    투자주의환기종목 종목코드 (코스닥).

    KIND 는 표 안에 종목코드를 그대로 적는다. 다만 페이지 어디에나 6자리
    숫자가 있을 수 있으므로, 표(<td>) 안에 홀로 들어 있는 것만 센다.
    """
    try:
        doc = fetch(ALERT_URL)
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::투자주의환기종목 가져오기 실패 ({type(e).__name__}: {e})")
        return set()
    if not doc:
        log("::warning::투자주의환기종목 페이지를 글자로 풀지 못했습니다")
        return set()
    out = set()
    for cell in re.findall(r"<td[^>]*>(.*?)</td>", doc, re.I | re.S):
        text = html.unescape(re.sub(r"<[^>]+>", " ", cell)).strip()
        if re.fullmatch(r"\d{6}", text):
            out.add(text)
    return out


def collect() -> dict:
    """
    {"관리종목": set, "투자주의환기종목": set, "source": {...}}

    source 에는 각각 몇 종목을 받았는지 적는다. 0 이면 그 조건은 제외에
    적용되지 않았다는 뜻이고, 그 사실이 결과 파일에 남아야 한다.
    """
    admin = admin_issues()
    alert = alert_issues()
    if not admin:
        log("::warning::관리종목을 하나도 받지 못했습니다 — 이번에는 "
            "관리종목이 걸러지지 않습니다")
    if not alert:
        log("::warning::투자주의환기종목을 하나도 받지 못했습니다 — 이번에는 "
            "환기종목이 걸러지지 않습니다")
    log(f"  관리종목 {len(admin)}종목 · 투자주의환기종목 {len(alert)}종목")
    return {"관리종목": admin, "투자주의환기종목": alert,
            "source": {"관리종목": len(admin), "투자주의환기종목": len(alert)}}


def dump() -> int:
    for name, url, pat in (("관리종목", ADMIN_URL, NAVER_CODE),
                           ("투자주의환기종목", ALERT_URL, KIND_CODE)):
        log(f"\n===== {name} {url} =====")
        try:
            doc = fetch(url)
        except Exception as e:                   # noqa: BLE001
            log(f"  가져오기 실패: {type(e).__name__}: {e}")
            continue
        if not doc:
            log("  글자로 풀지 못했습니다")
            continue
        t = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
        title = re.sub(r"\s+", " ", html.unescape(t.group(1))).strip() if t else "없음"
        log(f"  {len(doc)}자 · <title>: {title}")
        log(f"  <table> {len(re.findall(r'<table', doc, re.I))}개 · "
            f"<td> {len(re.findall(r'<td', doc, re.I))}개")
        log(f"  코드 패턴 매치 {len(set(pat.findall(doc)))}개")
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S)
        log(f"  <tr> {len(rows)}개 · 표본 3줄:")
        for r in rows[1:4]:
            cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.I | re.S)]
            log(f"    {cells[:7]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="관리종목·투자주의환기종목 목록")
    p.add_argument("--dump", action="store_true", help="원본 구조 확인")
    a = p.parse_args(argv)
    if a.dump:
        return dump()
    f = collect()
    log(f"관리종목 {sorted(f['관리종목'])[:10]} ...")
    log(f"환기종목 {sorted(f['투자주의환기종목'])[:10]} ...")
    return 0 if (f["관리종목"] and f["투자주의환기종목"]) else 1


if __name__ == "__main__":
    sys.exit(main())
