# -*- coding: utf-8 -*-
"""
거시 지표 — 무엇이 언제 어떻게 움직였고, 그 다음에 무슨 일이 있었나.

뉴스 목록 대신 **기록**을 둔다. "FOMC 가 7월 29일에 0.25%p 내렸다" 는 사실이고,
"그 전에도 아홉 번 내렸는데 그 뒤 20거래일 코스피가 여섯 번 올랐다" 도 사실이다.
둘을 나란히 두면 판단은 보는 사람이 한다. 앱은 판단하지 않는다.

**틀**
  지표(SERIES) 하나마다
    · 값의 흐름     — 차트로 그릴 점들
    · 사건(events)  — 값이 바뀐 날. 정책금리는 바뀐 날이 곧 결정일이다
    · 이후 기록      — 사건 뒤 20·60거래일 코스피가 어땠는지, 표본 몇 개로

지표를 늘리려면 SERIES 에 한 줄 더하면 된다. 금리로 시작하지만 환율·유가도
같은 틀이다.

**사건을 따로 받아오지 않는 이유**: 금리는 값이 바뀐 날이 곧 인상·인하일이다.
회의 일정표를 따로 긁어 맞추는 것보다 값에서 뽑는 편이 틀릴 여지가 없다.

    python market_macro.py              # 수집·저장
    python market_macro.py --dump       # 받아온 원본 확인
"""
from __future__ import annotations

import os
import io
import re
import sys
import csv
import json
import argparse
import datetime as dt

import requests

STORE_PATH = os.path.join("store", "market_macro.json")
# 코스피는 여기서 읽는다. market_signal 이 같은 실행에서 먼저 채운다.
SIGNAL_PATH = os.path.join("store", "market_signal.json")
DOCS_PATH = os.path.join("docs", "market_macro.json")

YEARS = 10               # 표본을 늘리려면 길어야 한다. 3년이면 결정이 스무 번뿐이다
AFTER = (20, 60)         # 사건 뒤 며칠을 볼지 (거래일)
CHART_POINTS = 400       # 화면에 보낼 점 수. 그보다 촘촘해도 폰에서 안 보인다
TIMEOUT = 30
UA = ("Mozilla/5.0 (compatible; kkujungbuja/1.0; "
      "+https://github.com/Tim-rich-2030/Company-Screener)")

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
# ECOS 는 무료 키가 필요하다. 없으면 한국 기준금리만 빠지고 나머지는 그대로 나온다.
ECOS_URL = ("https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000"
            "/{stat}/{cycle}/{start}/{end}/{item}")

# 지표 정의. step=True 면 '값이 바뀐 날'을 사건으로 본다 (정책금리처럼 계단 모양).
SERIES = [
    {"key": "us_rate", "name": "미국 기준금리", "unit": "%", "step": True,
     "fred": "DFEDTARU", "note": "연방기금목표금리 상단"},
    # ECOS 키가 있으면 한국은행 기준금리를 그대로 받는다. 키가 없거나 ECOS 가
    # 막혀 있으면 FRED 의 한국 단기금리로 대신하되, **이름과 설명을 바꿔 단다**.
    # 콜금리는 기준금리를 따라다니지만 같은 값이 아니다. 같은 이름을 달면
    # 대용을 원본으로 읽게 된다.
    {"key": "kr_rate", "name": "한국 기준금리", "unit": "%", "step": True,
     "ecos": {"stat": "722Y001", "cycle": "D", "item": "0101000"},
     "note": "한국은행 기준금리",
     # 대용은 **계단이 아니다.** 콜금리는 시장에서 매일 조금씩 움직이는 값이라,
     # 기준금리처럼 '값이 바뀐 날 = 결정일'로 보면 2.541 → 2.527 같은 미세 변동이
     # 전부 '인하'로 찍힌다. 실제로 그렇게 24건이 나왔다. 대용일 때는 선으로만
     # 그리고 사건을 잡지 않는다.
     "fallback": {"name": "한국 단기금리", "step": False,
                  "note": "기준금리 대용 — ECOS 키가 없어 FRED 의 한국 "
                          "콜/단기금리로 대신함. 시장금리라 매일 조금씩 움직인다",
                  "fred": ["IRSTCI01KRM156N", "IR3TIB01KRM156N",
                           "INTDSRKRM193N"]}},
    {"key": "usdkrw", "name": "원/달러", "unit": "원", "step": False,
     "fred": "DEXKOUS", "note": "매매기준율(FRED)"},
    {"key": "wti", "name": "WTI 유가", "unit": "달러", "step": False,
     "fred": "DCOILWTICO", "note": "서부텍사스산 현물"},
    # 금은 FRED 의 런던금(GOLDPMGBD228NLBM)이 2023년에 끊겼다. 과거 값은 아직
    # 주지만 최신값이 없어 머리에 띄우면 몇 년 전 값이 오늘 시세로 읽힌다.
    # 그래서 stooq 의 XAU/USD 를 먼저 보고, 안 되면 FRED 로 물러선다.
    # 금은 갈 곳이 마땅치 않다. FRED 의 런던금 두 계열은 404 로 없어졌고,
    # stooq 는 CSV 대신 봇 벽(noindex/noscript, 796자)을 준다. 네이버 금융의
    # 시장지표는 국제 금 시세를 날짜별로 표에 적어 둔다.
    {"key": "gold", "name": "금", "unit": "달러", "step": False,
     "naver_gold": True, "note": "국제 금 (네이버 금융 시장지표)"},
]

STOOQ_CSV = "https://stooq.com/q/d/l/?s={sym}&i=d"
GOLD_URL = ("https://finance.naver.com/marketindex/goldDailyQuote.naver"
            "?marketindexCd=CMDT_GC&page={page}")


# 표의 칸 이름에 단위가 적혀 있어야 값을 쓴다. 무엇을 읽는지 모르는 채로
# 숫자를 내보내지 않는다.
GOLD_OK = re.compile(r"(원|달러|USD|KRW|종가|매매기준율)")


def fetch_gold(start: dt.date, pages: int = 6, why: dict = None,
               unit: dict = None) -> dict:
    """
    국제 금 시세 — 네이버 금융 시장지표의 날짜별 표.

    한 쪽에 열흘쯤 들어 있어 몇 쪽을 이어 받는다. 표 구조를 짐작하지 않고
    줄마다 'YYYY.MM.DD' 와 그 뒤 첫 숫자를 짝지어 읽는다.
    """
    out = {}
    unit = unit if unit is not None else {}
    for page in range(1, pages + 1):
        try:
            r = requests.get(GOLD_URL.format(page=page),
                             headers={"User-Agent": UA,
                                      "Referer": "https://finance.naver.com/marketindex/"},
                             timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:                   # noqa: BLE001
            if why is not None:
                why[f"금/{page}쪽"] = f"{type(e).__name__}: {e}"[:140]
            break
        doc = r.content.decode("cp949", "replace")
        if page == 1:
            # 어느 칸을 읽고 있는지 확인되기 전에는 숫자를 내보내지 않는다.
            # 처음 붙였을 때 금값이 185,821.74 '달러' 로 나갔다 — 국제 금
            # (온스당 3천 달러대)이 아니라 다른 칸을 집은 것이다.
            heads = [re.sub(r"<[^>]+>", " ", h).strip()
                     for h in re.findall(r"<th[^>]*>(.*?)</th>", doc, re.I | re.S)]
            if why is not None:
                why["금/칸"] = " | ".join(h for h in heads if h)[:160]
            if not any(GOLD_OK.search(h) for h in heads):
                if why is not None:
                    why["금"] = ("칸 이름에서 단위를 확인하지 못해 값을 "
                                 "내보내지 않습니다 — 위 '금/칸' 을 보고 고칩니다")
                return {}
            unit["v"] = "원" if any("원" in h for h in heads) else "달러"
        got = 0
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.I | re.S):
            text = re.sub(r"<[^>]+>", " ", row)
            m = re.search(r"(20\d{2})\.(\d{2})\.(\d{2})", text)
            if not m:
                continue
            v = re.search(r"([\d,]+\.\d+)", text[m.end():])
            if not v:
                continue
            try:
                d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                val = float(v.group(1).replace(",", ""))
            except ValueError:
                continue
            if d >= start and val > 0:
                out[d.strftime("%Y%m%d")] = val
                got += 1
        if not got:                              # 더 볼 쪽이 없다
            break
    return out


def fetch_stooq(sym: str, start: dt.date, why: dict = None) -> dict:
    """
    stooq 도 키 없이 CSV 를 준다. Date,Open,High,Low,Close 로 온다.

    다만 심볼이 없거나 하루 한도를 넘기면 200 을 주면서 본문에 'No data' 나
    'Exceeded the daily hits limit' 만 적어 보낸다. 그러면 0건이 나오는데,
    왜 0건인지는 본문을 봐야 안다 — 앞머리를 진단에 남긴다.
    """
    r = requests.get(STOOQ_CSV.format(sym=sym),
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    head = " ".join(r.text.splitlines()[:2])[:120]
    if why is not None:
        why[f"stooq/{sym}"] = f"{r.status_code} · {len(r.text)}자 · {head}"
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            d = dt.date.fromisoformat(str(row.get("Date", "")).strip())
        except (ValueError, AttributeError):
            continue
        if d < start:
            continue
        try:
            out[d.strftime("%Y%m%d")] = float(row["Close"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def log(msg: str) -> None:
    print(msg, flush=True)


# =============================================================================
# 받아오기
# =============================================================================

def fetch_fred(series_id: str, start: dt.date) -> dict:
    """
    FRED 는 키 없이 CSV 를 준다. {YYYYMMDD: 값}.

    쉬는 날은 '.' 으로 온다. 그런 줄은 버린다 — 0 으로 채우면 금리가 0% 로
    떨어진 것처럼 보인다.
    """
    r = requests.get(FRED_CSV.format(id=series_id),
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        cols = list(row)
        if len(cols) < 2:
            continue
        day, val = row[cols[0]], row[cols[1]]
        try:
            d = dt.date.fromisoformat(day.strip())
        except (ValueError, AttributeError):
            continue
        if d < start:
            continue
        try:
            out[d.strftime("%Y%m%d")] = float(val)
        except (TypeError, ValueError):
            continue                      # '.' — 값이 없는 날
    return out


def fetch_ecos(spec: dict, start: dt.date, end: dt.date,
               why: dict = None) -> dict:
    """
    한국은행 ECOS. 키가 없으면 빈 dict — 그 지표만 빠진다.

    **키가 없는 것과, 키는 있는데 응답이 비는 것을 갈라 적는다.** 둘 다
    '대용을 씁니다'로 끝나서 화면만 봐서는 구분이 안 됐다.
    """
    key = os.environ.get("ECOS_KEY", "").strip()
    if not key:
        log("::warning::ECOS_KEY 가 없습니다 — 한국 기준금리는 비웁니다. "
            "발급: https://ecos.bok.or.kr/api  →  저장소 Secrets 에 ECOS_KEY")
        if why is not None:
            why["ECOS"] = "ECOS_KEY 환경변수가 비어 있음 (Secrets 이름 확인)"
        return {}
    if why is not None:
        why["ECOS"] = f"키 있음 (길이 {len(key)})"
    url = ECOS_URL.format(key=key, stat=spec["stat"], cycle=spec["cycle"],
                          start=start.strftime("%Y%m%d"),
                          end=end.strftime("%Y%m%d"), item=spec["item"])
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if "StatisticSearch" not in body:
        # ECOS 는 오류도 200 으로 준다. 키가 틀렸거나 통계표 코드가 틀린 경우다.
        log(f"::warning::ECOS 응답에 자료가 없습니다: {str(body)[:200]}")
        if why is not None:
            why["ECOS"] = f"키는 있는데 자료가 없음: {str(body)[:180]}"
        return {}
    out = {}
    for row in body["StatisticSearch"].get("row", []):
        day, val = row.get("TIME", ""), row.get("DATA_VALUE")
        if not re.fullmatch(r"\d{8}", day or ""):
            continue
        try:
            out[day] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def fetch_kospi(years: int = YEARS, diag: dict = None) -> dict:
    """
    코스피 일별 종가. 사건 이후 기록을 내려면 지수가 사건보다 길어야 한다.

    **이미 받아 둔 것을 쓴다.** market_signal 이 같은 실행에서 코스피 3년치를
    store/market_signal.json 에 넣어 두므로 그 파일을 읽는다.

        KRX 에서 직접 받으려고 두 번 고쳤는데 두 번 다 실패했다.
          · 한 번에 10년   → 빈 표
          · 해마다 한 번씩 → KeyError: '지수명' (지수 이름 조회에서 터짐)
          · 이름 조회를 꺼도 → 여전히 빈 표 (11년 전부)
        같은 함수를 market_signal 은 3년 한 번으로 멀쩡히 받는다. 무엇이
        다른지 알아내는 것보다 이미 받아 둔 것을 쓰는 편이 확실하고 KRX 도
        덜 두드린다. 대신 창이 10년에서 3년으로 줄어 사건 표본이 준다.
    """
    try:
        with open(SIGNAL_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"::warning::코스피를 읽지 못했습니다 ({type(e).__name__}) — "
            "이후 기록이 빕니다")
        if diag is not None:
            diag["읽기"] = f"{type(e).__name__}: {e}"[:120]
        return {}

    series = ((data.get("series") or {}).get("코스피")) or {}
    out = {}
    for day, v in series.items():
        # 저장값은 [시가,고가,저가,종가]. 종가 하나만 담던 옛 파일도 읽는다.
        close = v[3] if isinstance(v, list) and len(v) >= 4 else v
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close > 0:
            out[day] = close
    if diag is not None:
        diag["출처"] = f"{SIGNAL_PATH} · {len(out)}일"
    if not out:
        log("::warning::코스피가 비어 있습니다 — 이후 기록이 빕니다")
    return out


# =============================================================================
# 사건과 이후 기록
# =============================================================================

def changes(points: dict) -> list:
    """
    값이 바뀐 날. 정책금리는 바뀐 날이 곧 결정일이다.

    소수점 오차로 '바뀐 것처럼' 보이는 경우가 있어 0.001 미만은 같은 값으로 본다.
    """
    days = sorted(points)
    out, prev = [], None
    for d in days:
        v = points[d]
        if prev is not None and abs(v - prev) >= 0.001:
            out.append({"date": d, "from": round(prev, 4), "to": round(v, 4),
                        "dir": "인상" if v > prev else "인하"})
        prev = v
    return out


# 창 시작 바로 앞의 휴일·주말까지만 봐 준다. 연휴가 가장 길어야 이 정도다.
GRACE_DAYS = 7


def gap(a: str, b: str) -> int:
    """두 YYYYMMDD 사이의 날 수. 읽을 수 없으면 아주 큰 값 (= 봐 주지 않음)."""
    try:
        return abs((dt.datetime.strptime(b, "%Y%m%d")
                    - dt.datetime.strptime(a, "%Y%m%d")).days)
    except ValueError:
        return 10 ** 6


def after_returns(events: list, kospi: dict, spans=AFTER) -> dict:
    """
    사건 뒤 코스피가 어땠는지. **서술만 한다.**

    표본 수를 반드시 함께 낸다. 아홉 번 중 여섯 번은 '자주'가 아니라 '아홉 번 중
    여섯 번'이다. 표본이 3개 미만이면 통계를 내지 않는다 — 두 번 중 두 번 올랐다는
    말은 아무것도 알려주지 않으면서 확신만 준다.
    """
    if not kospi or not events:
        return {}
    days = sorted(kospi)
    pos = {d: i for i, d in enumerate(days)}

    def ret(day: str, n: int):
        # 지수가 있는 구간 밖의 사건은 **재지 않는다.**
        #
        #   'day 이후 첫 거래일'로만 잡아 뒀더니, 창(3년)보다 오래된 사건이
        #   전부 창의 첫날 하나로 몰렸다. 2016년 인상도 2018년 인상도 같은
        #   날에서 재게 되어 -4.97% 가 인상·인하·미국·한국 네 칸에 똑같이
        #   찍혔다. 표본 19회라고 적히지만 실제로는 같은 값 19개다.
        #   모르는 것은 재지 않는다.
        #
        #   다만 창 시작 바로 앞의 휴일·주말은 봐 준다. 결정일이 1월 1일이고
        #   지수가 1월 2일부터면 그건 같은 사건이다. 며칠까지만 봐 준다.
        if day < days[0] and gap(day, days[0]) > GRACE_DAYS:
            return None
        # 사건일이 휴장일 수 있다. 그 다음 거래일을 기준으로 잡는다.
        i = pos.get(day)
        if i is None:
            later = [d for d in days if d >= day]
            if not later:
                return None
            i = pos[later[0]]
        if i + n >= len(days):
            return None
        a, b = kospi[days[i]], kospi[days[i + n]]
        return (b / a - 1) * 100 if a else None

    out = {}
    for direction in ("인상", "인하"):
        picked = [e for e in events if e["dir"] == direction]
        row = {"n": len(picked), "spans": {}}
        for n in spans:
            vals = [v for v in (ret(e["date"], n) for e in picked) if v is not None]
            if len(vals) < 3:
                continue
            vals.sort()
            mid = vals[len(vals) // 2] if len(vals) % 2 else \
                (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
            row["spans"][str(n)] = {
                "표본": len(vals),
                "상승": sum(1 for v in vals if v > 0),
                "중앙값": round(mid, 2),
            }
        if row["spans"]:
            out[direction] = row
    return out


def thin(points: dict, keep: int = CHART_POINTS) -> list:
    """
    차트용으로 솎는다. [[YYYYMMDD, 값], ...]

    계단 모양(금리)은 바뀐 지점을 남겨야 하므로 여기서 쓰지 않는다.
    """
    days = sorted(points)
    if len(days) <= keep:
        return [[d, points[d]] for d in days]
    step = len(days) / keep
    idx = sorted({int(i * step) for i in range(keep)} | {len(days) - 1})
    return [[days[i], points[days[i]]] for i in idx]


def step_points(points: dict, events: list) -> list:
    """계단 모양은 시작점·변화점·끝점만 있으면 그대로 그려진다."""
    days = sorted(points)
    if not days:
        return []
    keys = [days[0]] + [e["date"] for e in events] + [days[-1]]
    seen, out = set(), []
    for d in keys:
        if d in points and d not in seen:
            seen.add(d)
            out.append([d, points[d]])
    return sorted(out)


# =============================================================================

def collect(years: int = YEARS) -> dict:
    end = dt.date.today()
    start = end - dt.timedelta(days=int(365.25 * years) + 10)
    kdiag = {}
    kospi = fetch_kospi(years, kdiag)
    log(f"[수집] 코스피 {len(kospi)}일치")

    out, failed, why = [], [], {}
    for spec in SERIES:
        spec = dict(spec)
        name = spec["name"]
        try:
            if spec.get("naver_gold"):
                unit = {}
                pts = fetch_gold(start, why=why, unit=unit)
                if unit.get("v"):
                    spec["unit"] = unit["v"]
                    spec["note"] = f"{spec['note']} · 단위 {unit['v']}"
            elif "stooq" in spec:
                syms = spec["stooq"]
                syms = syms if isinstance(syms, list) else [syms]
                pts = {}
                for sym in syms:
                    pts = fetch_stooq(sym, start, why)
                    if pts:
                        spec["note"] = f"{spec['note']} (stooq {sym})"
                        break
            elif "fred" in spec:
                pts = fetch_fred(spec["fred"], start)
            else:
                pts = fetch_ecos(spec["ecos"], start, end, why)
        except Exception as e:                   # noqa: BLE001
            log(f"::warning::{name} 실패 ({type(e).__name__}: {e})")
            why[name] = f"{type(e).__name__}: {e}"[:160]
            pts = {}

        # 원본을 못 받았을 때만 대용을 쓴다. 후보를 차례로 시도하고, 쓰게 되면
        # 이름과 설명을 대용의 것으로 바꿔 단다 — 대용을 원본 이름으로 내보내면
        # 보는 사람이 알 방법이 없다.
        if not pts and spec.get("fallback"):
            fb = spec["fallback"]
            for fid in fb["fred"]:
                try:
                    pts = fetch_fred(fid, start)
                except Exception as e:           # noqa: BLE001
                    log(f"  {name} 대용 후보 실패 {fid} ({type(e).__name__})")
                    why[f"{name}/{fid}"] = f"{type(e).__name__}: {e}"[:160]
                    continue
                if pts:
                    spec["name"] = name = fb["name"]
                    spec["note"] = f"{fb['note']} ({fid})"
                    spec["step"] = fb.get("step", spec["step"])
                    log(f"::warning::{name} — 원본 대신 대용을 씁니다: {fid}")
                    break

        if not pts:
            failed.append(name)
            why.setdefault(name, "받기는 했는데 값이 하나도 없음")
            log(f"  {name}: 없음")
            out.append({**{k: spec[k] for k in ("key", "name", "unit", "note")},
                        "points": [], "events": [], "after": {}, "last": None})
            continue

        evs = changes(pts) if spec["step"] else []
        chart = step_points(pts, evs) if spec["step"] else thin(pts)
        last_day = max(pts)
        item = {k: spec[k] for k in ("key", "name", "unit", "note")}
        item.update({
            "points": chart,
            "events": evs[-24:],                 # 최근 것만. 옛날 것은 통계로 남는다
            "after": after_returns(evs, kospi),
            "last": pts[last_day],
            "last_date": last_day,
            "span": [min(pts), last_day],
        })
        out.append(item)
        log(f"  {name}: {len(pts)}일치 · 변화 {len(evs)}회 · "
            f"최근 {pts[last_day]}{spec['unit']} ({last_day})")

    return {"as_of": end.strftime("%Y%m%d"), "years": years,
            "kospi_days": len(kospi), "kospi_diag": kdiag,
            "series": out, "failed": failed, "why": why}


def save(payload: dict) -> None:
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    # 진단(why·kospi_diag)은 store/ 에만 남긴다. 화면이 읽을 이유가 없고,
    # 로그는 다른 단계 출력에 밀려 정작 볼 때 안 보인다.
    slim = {k: v for k, v in payload.items() if k not in ("why", "kospi_diag")}
    os.makedirs(os.path.dirname(DOCS_PATH) or ".", exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (진단 포함), {DOCS_PATH}")


def dump() -> int:
    start = dt.date.today() - dt.timedelta(days=400)
    for spec in SERIES:
        log(f"\n===== {spec['name']}")
        try:
            if "stooq" in spec:
                r = requests.get(STOOQ_CSV.format(sym=spec["stooq"]),
                                 headers={"User-Agent": UA}, timeout=TIMEOUT)
                log(f"  stooq {spec['stooq']} → {r.status_code}, {len(r.text)}자")
                log("  앞 3줄: " + " | ".join(r.text.splitlines()[:3]))
            elif "fred" in spec:
                r = requests.get(FRED_CSV.format(id=spec["fred"]),
                                 headers={"User-Agent": UA}, timeout=TIMEOUT)
                log(f"  FRED {spec['fred']} → {r.status_code}, {len(r.text)}자")
                log("  앞 3줄: " + " | ".join(r.text.splitlines()[:3]))
            else:
                pts = fetch_ecos(spec["ecos"], start, dt.date.today())
                log(f"  ECOS → {len(pts)}건 {sorted(pts)[:3]}")
        except Exception as e:                   # noqa: BLE001
            log(f"  실패: {type(e).__name__}: {e}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="거시 지표 — 흐름·사건·이후 기록")
    p.add_argument("--years", type=int, default=YEARS)
    p.add_argument("--dump", action="store_true")
    a = p.parse_args(argv)
    if a.dump:
        return dump()

    payload = collect(a.years)
    save(payload)
    for s in payload["series"]:
        if not s["events"]:
            continue
        log(f"\n[{s['name']}] 최근 변화")
        for e in s["events"][-4:]:
            log(f"  {e['date']}  {e['dir']}  {e['from']} → {e['to']}{s['unit']}")
        for d, row in (s["after"] or {}).items():
            for n, v in row["spans"].items():
                log(f"  {d} {row['n']}회 → {n}거래일 뒤 상승 "
                    f"{v['상승']}/{v['표본']}회, 중앙값 {v['중앙값']:+.2f}%")
    if payload["failed"]:
        log(f"::warning::받지 못한 지표: {', '.join(payload['failed'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
