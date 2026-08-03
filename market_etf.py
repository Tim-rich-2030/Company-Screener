# -*- coding: utf-8 -*-
"""
ETF — 그날의 전 종목과, 무엇을 담고 있는지.

두 가지를 한다.
  1) 거래대금·거래량·등락률 순위          (한 번의 호출로 전부)
  2) 테마 연결 — 어떤 ETF 가 어느 테마인지  (이름으로 맞춰본다)

**'많이 사는 순' 에 대하여**: 거래대금은 사고판 것을 합친 값이다. 누가 얼마나
'샀는지'는 투자자별 매매동향을 따로 받아야 나온다. 여기서는 거래대금으로
줄을 세우고, 화면에도 '거래대금순'이라고 적는다. 순매수라고 적으면 거짓말이다.

**테마 연결에 대하여**: 테마 정의(themes.json)의 match 는 아직 실제 ETF 이름을
보지 못한 채 적은 임시값이다. --themes 로 무엇이 걸리고 무엇이 비는지 먼저
확인하고 고쳐야 한다. 지금 상태로 종목까지 분류하면, 틀린 분류가 맞는 것처럼
화면에 붙는다.

    python market_etf.py              # 수집·저장
    python market_etf.py --themes     # 테마 match 가 실제로 걸리는지 점검
    python market_etf.py --dump       # 받아온 원본 열 이름 확인
"""
from __future__ import annotations

import os
import re
import sys
import json
import argparse
import datetime as dt

STORE_PATH = os.path.join("store", "market_etf.json")
DOCS_PATH = os.path.join("docs", "market_etf.json")
THEMES_PATH = "themes.json"

TOP_N = 40              # 화면에 보낼 상위 개수
BACKOFF_DAYS = 10

# 테마 연결에서 빼는 것들. 거래대금 순위에는 그대로 남는다.
#
# 1) 파생·채권·환율·원자재 — '어느 테마냐'를 물어도 답이 없다.
#    첫 실행에서 '은행채' ETF 가 은행 테마에 걸렸다. 채권을 이름 하나로 거르면
#    안 되고, 'OO채' 꼴을 통째로 잡아야 한다.
# 2) 해외형 — 구성종목이 외국 주식이라 국내 종목을 채우는 데 쓸 수 없다.
#    첫 실행에서 '미국AI소프트웨어'가 AI SW 에, '차이나휴머노이드'가
#    휴머노이드에 걸렸다. 1,155개 중 466개가 해외형이다.
NOT_THEME = re.compile(
    r"레버리지|인버스|２[Xx]|2[Xx]|선물|"
    r"채권|(?:국고|통안|은행|회사|금융|특수|여전|산금|공사|물가|크레딧|단기|중기|장기)채|"
    r"CD금리|금리액티브|머니마켓|MMF|"
    r"달러|엔화|유로|위안|원유|금현물|금선물|은선물|"
    r"리츠|TR\b|고배당|배당|"
    r"미국|글로벌|차이나|중국|일본|인도|베트남|유럽|선진국|신흥국|"
    r"S&P|나스닥|필라델피아|다우|MSCI|아시아|대만|해외")


def log(msg: str) -> None:
    print(msg, flush=True)


def _stock():
    try:
        from pykrx import stock
        return stock
    except Exception as e:                       # noqa: BLE001
        log(f"::warning::pykrx 를 쓸 수 없습니다 ({type(e).__name__}: {e})")
        return None


def _raw(date: str):
    """
    KRX 전종목시세(ETF) 를 원본 그대로 받는다.

    pykrx 의 공개 함수 get_etf_ohlcv_by_ticker 는 티커만 남기고 **종목명과
    기초지수명을 버린다**. 둘 다 필요해서 내부 클래스를 직접 쓴다. 내부 API 라
    라이브러리가 바뀌면 깨질 수 있으므로, 열 이름이 없으면 조용히 비운다.
    """
    from pykrx.website.krx.etx.core import 전종목시세_ETF
    return 전종목시세_ETF().fetch(date)


def _num(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def collect(date: str = None) -> dict:
    stock = _stock()
    if stock is None:
        raise SystemExit("pykrx 없이는 ETF 를 받을 수 없습니다")

    start = dt.date.today()
    for i in range(BACKOFF_DAYS):
        d = date or (start - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = _raw(d)
        except Exception as e:                   # noqa: BLE001
            log(f"  {d}: 실패 ({type(e).__name__}: {e})")
            if date:
                raise SystemExit("ETF 시세를 받지 못했습니다")
            continue
        if df is not None and not df.empty and "ISU_SRT_CD" in df:
            rows = []
            for _, r in df.iterrows():
                val = _num(r.get("ACC_TRDVAL"))
                rows.append({
                    "code": str(r.get("ISU_SRT_CD")),
                    "name": str(r.get("ISU_ABBRV") or "").strip(),
                    "index": str(r.get("IDX_IND_NM") or "").strip(),
                    "chg": round(_num(r.get("FLUC_RT")), 2),
                    "close": int(_num(r.get("TDD_CLSPRC"))),
                    "vol": int(_num(r.get("ACC_TRDVOL"))),
                    "val": int(val),
                    "nav_total": int(_num(r.get("INVSTASST_NETASST_TOTAMT"))),
                })
            traded = [x for x in rows if x["val"] > 0]
            if traded:
                log(f"[수집] ETF {len(rows)}종목 ({d}) · 거래된 것 {len(traded)}")
                return {"date": d, "source": "pykrx", "items": rows}
            log(f"  {d}: 거래 없음 (휴장)")
        if date:
            break
    raise SystemExit("ETF 시세를 받지 못했습니다")


# =============================================================================
# 테마 연결
# =============================================================================

def load_themes(path: str = THEMES_PATH) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["themes"]


def match_themes(items: list, themes: list) -> dict:
    """
    ETF 이름을 테마 소분류의 match 조각과 맞춰본다.

    한 ETF 가 여러 소분류에 걸릴 수 있다 (예: '반도체소부장' 은 '소부장' 과
    '반도체TOP' 둘 다에 걸린다). 그럴 때는 **가장 긴 조각**이 이긴다 —
    긴 조각일수록 좁은 뜻이다.
    """
    out = {}
    for th in themes:
        for sub in th["subs"]:
            out[(th["name"], sub["name"])] = []
    for it in items:
        if not it["name"] or NOT_THEME.search(it["name"]):
            continue
        best, best_len = None, 0
        for th in themes:
            for sub in th["subs"]:
                for frag in sub.get("match", []):
                    if frag and frag.lower() in it["name"].lower() \
                            and len(frag) > best_len:
                        best, best_len = (th["name"], sub["name"]), len(frag)
        if best:
            out[best].append(it)
    return out


def build(raw: dict, top_n: int = TOP_N) -> dict:
    items = raw["items"]
    traded = [i for i in items if i["val"] > 0]
    by_val = sorted(traded, key=lambda i: -i["val"])[:top_n]
    by_vol = sorted(traded, key=lambda i: -i["vol"])[:top_n]
    return {
        "date": raw["date"],
        "source": raw["source"],
        "counted": {"전체": len(items), "거래된 것": len(traded)},
        # 거래대금은 사고판 것의 합이다. '많이 산 순'이 아니다.
        "거래대금순": by_val,
        "거래량순": by_vol,
    }


def save(payload: dict, full: dict = None) -> None:
    for path, data in ((DOCS_PATH, payload), (STORE_PATH, full or payload)):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[저장] {STORE_PATH} (전체), {DOCS_PATH} (화면용)")


def report_themes(raw: dict, themes: list) -> int:
    """
    테마 match 가 실제 ETF 이름에 걸리는지 점검한다.

    비어 있는 소분류가 곧 '고쳐야 할 곳'이다. 지금은 실제 이름을 못 본 채
    적은 조각들이라 상당수가 빌 것으로 본다.
    """
    hit = match_themes(raw["items"], themes)
    empty = []
    for th in themes:
        log(f"\n[{th['name']}]")
        for sub in th["subs"]:
            got = hit[(th["name"], sub["name"])]
            if not got:
                empty.append(f"{th['name']}/{sub['name']}")
                log(f"  {sub['name']:16} — 걸린 ETF 없음  (match={sub['match']})")
            else:
                names = ", ".join(g["name"] for g in got[:4])
                log(f"  {sub['name']:16} {len(got):3}개  {names}")
    log(f"\n비어 있는 소분류 {len(empty)}개: {', '.join(empty)}")
    return 0


def dump(raw_df) -> int:
    log(f"열 이름: {list(raw_df.columns)}")
    log(raw_df.head(5).to_string()[:1500])
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="ETF 수집·테마 점검")
    p.add_argument("--date")
    p.add_argument("--top", type=int, default=TOP_N)
    p.add_argument("--themes", action="store_true", help="테마 match 점검")
    p.add_argument("--dump", action="store_true", help="원본 열 이름 확인")
    a = p.parse_args(argv)

    date = a.date.replace("-", "") if a.date else None
    if a.dump:
        s = _stock()
        if s is None:
            return 1
        d = date or dt.date.today().strftime("%Y%m%d")
        return dump(_raw(d))

    raw = collect(date)
    if a.themes:
        return report_themes(raw, load_themes())

    payload = build(raw, a.top)
    save(payload, {**payload, "items": raw["items"]})
    log(f"\n{payload['date']} · ETF {payload['counted']['전체']}종목 "
        f"(거래된 것 {payload['counted']['거래된 것']})")
    log("거래대금 상위 5:")
    for e in payload["거래대금순"][:5]:
        log(f"  {e['name'][:28]:28} {e['chg']:+6.2f}%  "
            f"{e['val']/1e8:10,.0f}억  [{e['index'][:22]}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
