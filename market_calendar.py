# -*- coding: utf-8 -*-
"""
캘린더 — 실적 시즌과 통화정책 회의.

두 갈래를 담는다.

  1) 실적 — **법정 제출기한**이다. '이 회사가 몇 일에 발표한다'가 아니다.
     상장사는 발표일을 미리 알리지 않는 곳이 대부분이라, 확실한 것은 법으로
     정해진 마감뿐이다 (자본시장법 제160조).
         분기·반기보고서 — 분기/반기 종료 후 45일
         사업보고서     — 사업연도 종료 후 90일
     여기에 '우리가 모은 292곳 중 몇 곳이 이미 냈나'를 붙인다. 마감이 남았는데
     대부분 이미 냈다면 그 분기 실적 시즌은 사실상 끝난 것이다.

  2) 통화정책 — FOMC 와 한국은행 금통위.
     둘 다 연간 일정을 미리 공표한다. 다만 **지어낼 수 없는 값**이므로 반드시
     원문에서 받아온다. 못 받으면 그 칸을 비운다.

    python market_calendar.py            # 받아서 저장
    python market_calendar.py --dump     # 파싱이 깨졌을 때 원본 구조 확인
"""
from __future__ import annotations

import os
import re
import time
import sys
import html
import json
import glob
import argparse
import datetime as dt

import requests

STORE_PATH = os.path.join("store", "market_calendar.json")
DOCS_PATH = os.path.join("docs", "market_calendar.json")
FACTS_DIR = os.path.join("store", "facts")

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
# 미국 지표 발표일. 노동통계국이 한 해 일정을 한 장에 다 적어 둔다.
BLS_URL = "https://www.bls.gov/schedule/news_release/{year}_sched.htm"

# 이 넷만 가져온다. 노동통계국은 한 해 200건 넘게 내는데 대부분 지역·업종
# 세부 통계라 캘린더가 그것으로 덮인다. 자주 회자되는 것만 남긴다.
BLS_KEEP = [
    ("consumer price index", "미국 소비자물가(CPI)"),
    ("producer price index", "미국 생산자물가(PPI)"),
    ("employment situation", "미국 고용보고서"),
    ("job openings", "미국 구인·이직(JOLTS)"),
]
BOK_URL = ("https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do"
           "?mtgSe=A&menuNo=200755")

TIMEOUT = 20
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

HANGUL = re.compile(r"[가-힣]")
ALIAS = {"euc-kr": "cp949", "ks_c_5601-1987": "cp949", "euckr": "cp949"}

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 실적 — 법정 제출기한
# =============================================================================

def add_days(d: dt.date, n: int) -> dt.date:
    return d + dt.timedelta(days=n)


def periods(today: dt.date, back: int = 2, ahead: int = 2) -> list:
    """
    최근·다가올 보고 기간의 마감일.

    분기 말일에서 45일(사업보고서는 90일)을 더한다. 마감이 주말·공휴일이면
    다음 영업일로 밀리지만, 공휴일표가 없으므로 **밀지 않는다**. 하루 이틀
    당겨 보여주는 편이 지나서 보여주는 것보다 낫다.
    """
    ends = []
    for y in (today.year - 1, today.year, today.year + 1):
        ends += [(dt.date(y, 3, 31), f"{y}년 1분기", 45),
                 (dt.date(y, 6, 30), f"{y}년 반기", 45),
                 (dt.date(y, 9, 30), f"{y}년 3분기", 45),
                 (dt.date(y, 12, 31), f"{y}년 사업보고서", 90)]
    rows = [{"period": name, "end": e.strftime("%Y%m%d"),
             "deadline": add_days(e, days).strftime("%Y%m%d"),
             "dday": (add_days(e, days) - today).days}
            for e, name, days in ends]
    rows.sort(key=lambda r: r["deadline"])
    past = [r for r in rows if r["dday"] < 0][-back:]
    future = [r for r in rows if r["dday"] >= 0][:ahead]
    return past + future


def quarter_label(period_end: str) -> str:
    """'20260630' → '2026Q2'. store/facts 의 분기 키와 맞추기 위한 변환."""
    y, m = int(period_end[:4]), int(period_end[4:6])
    return f"{y}Q{(m - 1) // 3 + 1}"


def filed_counts(rows: list) -> list:
    """
    우리가 모은 종목 중 그 분기 실적이 들어온 곳이 몇 곳인지.

    DART 를 다시 부르지 않는다. 이미 store/facts 에 있는 것만 센다.
    """
    files = glob.glob(os.path.join(FACTS_DIR, "*.json"))
    total = len(files)
    have = {}
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                qs = json.load(fp).get("quarters") or {}
        except Exception:                        # noqa: BLE001
            continue
        for q in qs:
            have[q] = have.get(q, 0) + 1
    for r in rows:
        q = quarter_label(r["end"])
        r["quarter"] = q
        r["filed"] = have.get(q, 0)
        r["total"] = total
    return rows


# =============================================================================
# 통화정책 — FOMC · 금통위
# =============================================================================

def decode(raw: bytes, declared: str = "") -> str | None:
    """market_news.py 와 같은 규칙. 엄격하게 풀고 못 풀면 None."""
    cands = []
    if declared:
        cands.append(declared)
    m = re.search(rb'charset=["\']?([\w.-]+)', raw[:4096], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    cands += ["utf-8", "cp949"]
    for enc in cands:
        enc = ALIAS.get(enc.strip().lower(), enc)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def fetch(url: str, tries: int = 2) -> str | None:
    """
    한 번 더 두드린다.

    금통위 페이지를 수집 단계에서는 못 받고, 2분 뒤 진단 단계에서는 멀쩡히
    받은 적이 있다. 코드가 아니라 그때 한 번 안 온 것이었다. 한 번의 실패로
    그날 일정을 통째로 비우지 않는다.
    """
    last = None
    for i in range(tries):
        try:
            return _fetch_once(url)
        except Exception as e:                   # noqa: BLE001
            last = e
            if i + 1 < tries:
                time.sleep(1.5)
    raise last


def _fetch_once(url: str) -> str | None:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", r.headers.get("content-type", ""), re.I)
    return decode(r.content, m.group(1) if m else "")


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def fomc_dates(today: dt.date) -> list:
    """
    FOMC 회의일.

    연준 페이지는 월 이름과 날짜를 따로 적는다 (예: 'January' / '27-28').
    회의는 이틀짜리가 대부분이고, 금리 결정은 **둘째 날**에 나온다. 그래서
    마지막 날을 회의일로 잡는다.
    """
    try:
        doc = fetch(FOMC_URL)
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::FOMC 가져오기 실패 ({type(e).__name__}: {e})")
        return []
    if not doc:
        log("::warning::FOMC 페이지를 글자로 풀지 못했습니다")
        return []

    # 태그를 다 벗기고 **한 줄로 이어서** 훑는다. 덩어리로 잘라 읽으려 했더니
    # 연도 표제('2026 FOMC Meetings')와 날짜가 서로 다른 덩어리로 떨어져서
    # 아무것도 못 찾았다. 원문은 연도가 먼저 나오고 그 아래 달들이 이어지므로,
    # 가장 최근에 본 연도를 기억하며 순서대로 읽으면 된다.
    mon = "|".join(MONTHS)
    token = re.compile(
        rf"(?P<year>20\d{{2}})\s*FOMC"
        rf"|(?P<m1>{mon})\s*(?P<d1>\d{{1,2}})"
        rf"(?:\s*[-–]\s*(?:(?P<m2>{mon})\s*)?(?P<d2>\d{{1,2}}))?")

    out, year = [], None
    for m in token.finditer(strip_tags(doc)):
        if m.group("year"):
            year = int(m.group("year"))
            continue
        if year is None:
            continue
        # 회의는 이틀이고 금리 결정은 둘째 날에 나온다. 끝나는 날을 잡는다.
        # 'January 31-February 1' 처럼 달을 넘기면 둘째 달을 쓴다.
        month = MONTHS[m.group("m2") or m.group("m1")]
        day = int(m.group("d2") or m.group("d1"))
        try:
            out.append(dt.date(year, month, day))
        except ValueError:
            continue
    return [{"date": d.strftime("%Y%m%d"), "name": "FOMC",
             "dday": (d - today).days} for d in sorted(set(out))]


def bok_dates(today: dt.date) -> list:
    """
    한국은행 금융통화위원회(통화정책방향) 회의일.

    페이지가 표로 날짜를 적는다. 'YYYY. M. D.' 또는 'YYYY-MM-DD' 꼴을 훑는다.
    """
    try:
        doc = fetch(BOK_URL)
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::금통위 가져오기 실패 ({type(e).__name__}: {e})")
        return []
    if not doc:
        log("::warning::금통위 페이지를 글자로 풀지 못했습니다")
        return []
    # 날짜는 표 안에 있다. 페이지 전체를 훑으면 주소·전화번호 같은 숫자가 섞이니
    # <td> 안만 본다. 첫 시도에서 'YYYY.M.D' 하나만 찾다가 0건이 나왔는데,
    # 표는 9행이 멀쩡히 있었다 — 형식이 달랐다는 뜻이다. 그래서 세 가지를 본다.
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", doc, re.I | re.S)
    text = " ".join(strip_tags(c) for c in cells) or strip_tags(doc)

    # 표에 연도가 없고 'M월 D일' 만 있는 경우를 위해 제목·본문에서 연도를 찾는다.
    ctx = re.search(r"(20\d{2})\s*년", strip_tags(doc))
    ctx_year = int(ctx.group(1)) if ctx else today.year

    out = set()
    for m in re.finditer(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
                         text):
        try:
            out.add(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    # 연도 없는 'M월 D일' 도 **함께** 모은다. 앞 형식이 하나라도 걸리면 건너뛰게
    # 해 뒀더니, 페이지 구석의 '최종수정일' 하나만 잡고 정작 표는 못 읽었다.
    for m in re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text):
        try:
            out.add(dt.date(ctx_year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            continue

    # 회의 일정이라면 최근·가까운 앞날에 모여 있어야 한다. 250일 지난 날짜
    # 하나만 남는 것은 표를 못 읽었다는 뜻이지 '회의가 그것뿐'이라는 뜻이 아니다.
    keep = sorted(d for d in out if -90 <= (d - today).days <= 400)
    if not keep:
        log(f"::warning::금통위 날짜를 {len(out)}개 찾았지만 쓸 만한 것이 "
            "없습니다 — 표를 못 읽은 것으로 봅니다")
        return []
    return [{"date": d.strftime("%Y%m%d"), "name": "한은 금통위",
             "dday": (d - today).days} for d in keep]


US_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),?\s+(20\d{2})", re.I)


def bls_dates(today: dt.date) -> list:
    """
    미국 지표 발표일 — 소비자물가·생산자물가·고용보고서·구인이직.

    노동통계국이 한 해 일정을 한 장에 적어 둔다. 표 구조를 짐작하지 않고,
    줄마다 글자를 이어 붙여 '월 일, 연도' 와 이름을 함께 찾는다. 이름이
    우리가 고른 넷에 안 걸리면 버린다.

    발표 시각은 미국 동부시간이라 한국에서는 대개 그날 밤이다. 시각까지
    옮기면 시차를 잘못 적을 수 있어 날짜만 쓴다.
    """
    out = []
    for year in (today.year, today.year + 1):
        try:
            doc = fetch(BLS_URL.format(year=year))
        except Exception as e:                   # noqa: BLE001
            log(f"  미국 지표 {year} 실패 ({type(e).__name__}: {e})")
            continue
        if not doc:
            continue
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S) or [doc]
        for row in rows:
            text = strip_tags(row)
            low = text.lower()
            name = next((ko for key, ko in BLS_KEEP if key in low), None)
            if not name:
                continue
            m = US_DATE.search(text)
            if not m:
                continue
            try:
                d = dt.date(int(m.group(3)), MONTHS[m.group(1).capitalize()],
                            int(m.group(2)))
            except (ValueError, KeyError):
                continue
            out.append({"date": d.strftime("%Y%m%d"), "name": name,
                        "dday": (d - today).days})
    # 같은 발표가 여러 줄에 걸릴 수 있다. 날짜+이름으로 한 번만 남긴다.
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: e["date"]):
        key = (e["date"], e["name"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    if not uniq:
        log("::warning::미국 지표 일정을 하나도 읽지 못했습니다")
    return uniq


def upcoming(events: list, today: dt.date, back: int = 1, ahead: int = 3) -> list:
    """지난 것 하나와 다가올 것 몇 개만. 연간 일정을 다 보여줄 이유가 없다."""
    past = [e for e in events if e["dday"] < 0][-back:]
    future = [e for e in events if e["dday"] >= 0][:ahead]
    return past + future


# =============================================================================

def collect(today: dt.date = None) -> dict:
    today = today or dt.date.today()
    rows = filed_counts(periods(today))

    fomc = fomc_dates(today)
    bok = bok_dates(today)
    bls = bls_dates(today)
    failed = []
    if not fomc:
        failed.append("FOMC")
    if not bok:
        failed.append("금통위")
    if not bls:
        failed.append("미국 지표")
    log(f"  FOMC {len(fomc)}건 · 금통위 {len(bok)}건 · 미국 지표 {len(bls)}건")

    # 미국 지표는 종류마다 매달 한 번씩이라 앞뒤로 넉넉히 담는다. 화면이
    # 월별로 펼쳐 보는 용도라 서너 건만 남기면 달력이 텅 빈다.
    events = sorted(upcoming(fomc, today) + upcoming(bok, today) +
                    upcoming(bls, today, back=2, ahead=14),
                    key=lambda e: e["date"])
    return {
        "as_of": today.strftime("%Y%m%d"),
        "실적": rows,
        "일정": events,
        "failed": failed,
        "note": "실적은 법정 제출기한입니다. 회사가 알린 발표일이 아닙니다.",
    }


def save(payload: dict) -> None:
    for path in (STORE_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH}, {DOCS_PATH}")


def dump() -> int:
    for name, url in (("FOMC", FOMC_URL), ("금통위", BOK_URL),
                      ("미국 지표", BLS_URL.format(year=dt.date.today().year))):
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
        log(f"  {len(doc)}자 · <title>: {strip_tags(t.group(1)) if t else '없음'}")
        log(f"  <table> {len(re.findall(r'<table', doc, re.I))}개 · "
            f"<tr> {len(re.findall(r'<tr', doc, re.I))}개")
        text = strip_tags(doc)
        ymd = re.findall(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}", text)
        mon = re.findall("|".join(MONTHS), text)
        log(f"  'YYYY.M.D' 꼴 {len(ymd)}개 · 영문 월 이름 {len(mon)}개")
        if ymd:
            log(f"    표본: {ymd[:8]}")
        # 표가 있으면 표를 보여준다. 본문 앞부분은 대개 메뉴라 쓸모가 없었다 —
        # 금통위 페이지가 정확히 그랬다 (표 9행이 멀쩡히 있는데 메뉴만 찍혔다).
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S)
        if rows:
            log(f"  표 {len(rows)}행 중 앞 8행:")
            for r in rows[:8]:
                cells = [strip_tags(c) for c in
                         re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.I | re.S)]
                log(f"    {[c[:30] for c in cells][:6]}")
        else:
            log("  본문 앞 600자:")
            log("    " + text[:600])
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="실적·통화정책 캘린더")
    p.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--dump", action="store_true")
    a = p.parse_args(argv)
    if a.dump:
        return dump()

    today = (dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    payload = collect(today)
    save(payload)

    log(f"\n{payload['as_of']} 기준")
    for r in payload["실적"]:
        when = f"D{r['dday']:+d}" if r["dday"] else "오늘"
        log(f"  {r['period']:16} 마감 {r['deadline']} ({when}) · "
            f"{r['filed']}/{r['total']}곳 수집됨")
    for e in payload["일정"]:
        log(f"  {e['date']}  {e['name']}  (D{e['dday']:+d})")
    if payload["failed"]:
        log(f"::warning::받지 못한 일정: {', '.join(payload['failed'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
