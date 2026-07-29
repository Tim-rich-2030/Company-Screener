#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
코스피 분기 실적 대시보드
=========================

종목별 분기 실적 시계열을 DART에서 모아 정규화해 저장하고, 그 위에서
밸류에이션 지표를 계산해 한 장의 웹페이지로 보여준다.

설계 원칙: 수집(collect)과 계산(metrics)을 완전히 분리한다
------------------------------------------------------------
지표를 하나 추가할 때 DART를 다시 부르면 30분씩 걸린다. 그래서 원천 데이터를
한 번 받아 `data/fundamentals_YYYYMMDD.json` 으로 저장해두고, 지표는 그 파일
위에서만 계산한다. 새 지표를 넣고 싶으면 함수 하나를 등록하고 렌더만 다시 돌리면
된다 (수 초). 이것이 '원할 때마다 열을 추가한다'는 요구의 실질이다.

    수집 (느림, 가끔)          계산·렌더 (빠름, 자주)
    ┌──────────────┐      ┌───────────────────────┐
    │ DART 분기보고서 │ ───> │ @metric 로 등록된 지표들 │ ───> dashboard.html
    │ 시장 종가/시총  │ json │ PBR PER ROE ...        │
    └──────────────┘      └───────────────────────┘

지표 추가 방법
--------------
    from quarterly_dashboard import metric

    @metric("PSR", desc="시가총액 / 최근 4분기 매출액", fmt="{:.2f}", better="low")
    def psr(c):
        rev = c.ttm("매출액")
        return c.mcap / rev if rev else None

`metrics_custom.py` 에 넣어두면 자동으로 읽어 열이 하나 늘어난다.

실행
----
    python quarterly_dashboard.py collect     # DART 수집 (오래 걸림)
    python quarterly_dashboard.py render      # 저장된 데이터로 페이지만 다시 생성
    python quarterly_dashboard.py all         # 수집 + 렌더

    코랩:  %run quarterly_dashboard.py all
"""

from __future__ import annotations

import os
import sys
import json
import glob
import math
import html
import datetime as dt
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1단계 스크리너의 시장 데이터·DART 계층을 그대로 재사용한다 (중복 구현 금지).
import kospi_value_screener as base
from kospi_value_screener import (  # noqa: F401
    log, to_num, norm_name, redact, EOK,
    DartClient, DART_FATAL_STATUS, Period,
    build_period_candidates, fetch_corp_code_map, fetch_market_snapshot,
    resolve_base_date, get_api_key, tqdm, pd,
)

# =============================================================================
# 설정
# =============================================================================

MIN_MARKET_CAP_KRW = 500_000_000_000    # 대상 종목 시총 하한 (5,000억)
QUARTERS = 8                            # 표시할 분기 수 (8 = 2년, 전년 동기 비교 가능)
REPORTS_PER_TICKER = 6                  # 종목당 조회할 보고서 수 (아래 주석 참고)
DART_WORKERS = 6                        # 동시 조회 수. 수집 시간에 직결된다

DATA_DIR = "data"
OUTPUT_HTML = "dashboard.html"
CUSTOM_METRICS_MODULE = "metrics_custom"   # 있으면 자동으로 읽는다

# 수집할 손익 계정. 여기에 추가하면 지표에서 c.ttm("계정명") 으로 바로 쓸 수 있다.
IS_ACCOUNTS = {
    "매출액": (
        {"ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"},
        ("매출액", "수익매출액", "영업수익", "매출"),
    ),
    "영업이익": (
        base.OPERATING_PROFIT_IDS,
        base.OPERATING_PROFIT_NAMES,
    ),
    "순이익": (
        {"ifrs-full_ProfitLoss"},
        ("당기순이익", "당기순이익손실", "분기순이익", "반기순이익", "당기순손익"),
    ),
    # 계정명은 모호한 것을 넣지 않는다. 포괄손익계산서에는 '지배기업 소유주지분'이
    # 총포괄손익 귀속액으로도 등장해서, 그걸 순이익으로 잘못 잡으면 PER이 틀어진다.
    "지배주주순이익": (
        {"ifrs-full_ProfitLossAttributableToOwnersOfParent"},
        ("지배기업의소유주에게귀속되는당기순이익",
         "지배기업의소유주에게귀속되는당기순이익손실",
         "지배기업소유주지분순이익", "지배주주지분순이익"),
    ),
}

# 재무상태표에서 추가로 담아둘 계정 (지표에서 c.bs("자산총계") 로 접근)
BS_ACCOUNTS = {
    "자산총계": ({"ifrs-full_Assets"}, ("자산총계",)),
    "부채총계": ({"ifrs-full_Liabilities"}, ("부채총계",)),
}


# =============================================================================
# 지표 레지스트리 — '열을 추가한다'의 실체
# =============================================================================

@dataclass
class Metric:
    key: str
    fn: object
    desc: str = ""
    fmt: str = "{:.2f}"
    better: str = ""        # "low" | "high" | "" (정렬 힌트, 색상 표시에 사용)
    group: str = "밸류에이션"
    default_on: bool = True


METRICS: dict[str, Metric] = {}


def metric(key: str, desc: str = "", fmt: str = "{:.2f}", better: str = "",
           group: str = "밸류에이션", default_on: bool = True):
    """
    지표를 열로 등록하는 데코레이터.

    함수는 MetricContext 하나를 받아 숫자 또는 None(계산 불가)을 돌려주면 된다.
    같은 key로 다시 등록하면 덮어쓴다 (사용자 정의가 기본 지표를 이길 수 있다).
    """
    def deco(fn):
        METRICS[key] = Metric(key=key, fn=fn, desc=desc, fmt=fmt,
                              better=better, group=group, default_on=default_on)
        return fn
    return deco


class MetricContext:
    """지표 함수에 넘어가는 한 종목분의 데이터."""

    def __init__(self, rec: dict, quarters: list[str]):
        self._rec = rec
        self._q = rec.get("quarters", {})      # {"2026Q1": {계정: 값}}
        self._order = quarters                 # 최신순 분기 라벨
        self.code = rec.get("code", "")
        self.name = rec.get("name", "")
        self.price = rec.get("price") or 0.0
        self.mcap = rec.get("mcap") or 0.0
        self.shares = rec.get("shares") or 0.0

    # --- 재무상태표 ---
    @property
    def equity(self) -> float | None:
        """자기자본 = 지배주주지분 우선, 없으면 자본총계."""
        bs = self._rec.get("balance") or {}
        return bs.get("지배주주지분") or bs.get("자본총계")

    def bs(self, account: str) -> float | None:
        return (self._rec.get("balance") or {}).get(account)

    # --- 분기 시계열 ---
    def q(self, account: str, n: int = 0) -> float | None:
        """n분기 전 값. n=0이 최신 분기."""
        if n >= len(self._order):
            return None
        return self._q.get(self._order[n], {}).get(account)

    def series(self, account: str, n: int = 0) -> list:
        """최신순 분기 값 목록 (없는 분기는 None)."""
        labels = self._order[:n] if n else self._order
        return [self._q.get(lb, {}).get(account) for lb in labels]

    def ttm(self, account: str) -> float | None:
        """최근 4분기 합계. 한 분기라도 비면 None."""
        vals = self.series(account, 4)
        if len(vals) < 4 or any(v is None for v in vals):
            return None
        return sum(vals)

    def ttm_prev(self, account: str) -> float | None:
        """직전 4분기(5~8분기 전) 합계. YoY 비교용."""
        vals = self.series(account)[4:8]
        if len(vals) < 4 or any(v is None for v in vals):
            return None
        return sum(vals)

    def yoy(self, account: str) -> float | None:
        """최신 분기의 전년 동기 대비 증감률(%). 전년 동기 = 4분기 전."""
        cur, prev = self.q(account, 0), self.q(account, 4)
        if cur is None or prev is None or prev == 0:
            return None
        return (cur - prev) / abs(prev) * 100


# =============================================================================
# 기본 지표 — 여기를 따라 쓰면 새 지표가 된다
# =============================================================================

@metric("PBR", desc="시가총액 / 자기자본(지배주주지분 우선). 재무제표에서 직접 계산",
        fmt="{:.2f}", better="low")
def m_pbr(c):
    eq = c.equity
    return c.mcap / eq if eq and eq > 0 else None


@metric("PER", desc="시가총액 / 최근 4분기 지배주주순이익 (TTM). 적자면 공란",
        fmt="{:.1f}", better="low")
def m_per(c):
    ni = c.ttm("지배주주순이익") or c.ttm("순이익")
    return c.mcap / ni if ni and ni > 0 else None


@metric("ROE(%)", desc="최근 4분기 지배주주순이익 / 자기자본 × 100",
        fmt="{:.1f}", better="high")
def m_roe(c):
    ni = c.ttm("지배주주순이익") or c.ttm("순이익")
    eq = c.equity
    return ni / eq * 100 if ni is not None and eq and eq > 0 else None


@metric("영업이익률(%)", desc="최근 4분기 영업이익 / 매출액 × 100",
        fmt="{:.1f}", better="high", group="수익성")
def m_opm(c):
    op, rev = c.ttm("영업이익"), c.ttm("매출액")
    return op / rev * 100 if op is not None and rev else None


@metric("매출성장률(%)", desc="최근 4분기 매출액 vs 직전 4분기 (TTM YoY)",
        fmt="{:+.1f}", better="high", group="성장성")
def m_rev_growth(c):
    cur, prev = c.ttm("매출액"), c.ttm_prev("매출액")
    return (cur - prev) / abs(prev) * 100 if cur is not None and prev else None


@metric("영업이익 YoY(%)", desc="최신 분기 영업이익의 전년 동기 대비 증감률",
        fmt="{:+.1f}", better="high", group="성장성")
def m_op_yoy(c):
    return c.yoy("영업이익")


@metric("영업흑자 분기", desc=f"최근 {QUARTERS}분기 중 영업이익이 흑자였던 분기 수",
        fmt="{:.0f}", better="high", group="수익성")
def m_profit_quarters(c):
    vals = [v for v in c.series("영업이익") if v is not None]
    return sum(1 for v in vals if v > 0) if vals else None


@metric("부채비율(%)", desc="부채총계 / 자기자본 × 100",
        fmt="{:.0f}", better="low", group="안정성", default_on=False)
def m_debt_ratio(c):
    debt, eq = c.bs("부채총계"), c.equity
    return debt / eq * 100 if debt is not None and eq and eq > 0 else None


# =============================================================================
# 수집: DART 분기 손익 + 재무상태표
# =============================================================================
#
# 분기 단독 실적은 '누계 차분'으로 구한다.
#   1Q누계=Q1, 반기누계=Q1+Q2, 3Q누계=Q1+Q2+Q3, 연간=Q1+..+Q4
#   => Q2 = 반기누계 - 1Q누계,  Q4 = 연간 - 3Q누계
# thstrm_add_amount(당기 누계)를 우선 쓰고 없으면 thstrm_amount로 대체한다.
# 각 보고서는 전기(frmtrm) 누계도 함께 담고 있어 한 번 조회로 2개년치가 나온다.

QUARTER_OF_REPRT = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def _match(item, ids: set, names: tuple) -> bool:
    acc_id = (item.get("account_id") or "").strip()
    if acc_id in ids:
        return True
    nm = norm_name(item.get("account_nm"))
    return nm in names


def parse_cumulative_is(payload: dict, year: int, quarter: int) -> dict:
    """
    손익계산서에서 계정별 '누계' 값을 뽑는다.
    반환: {(연도, 분기): {계정: 누계값}}  — 당기와 전기(전년 동기) 두 벌.
    """
    if payload.get("status") != "000":
        return {}

    cur: dict[str, float] = {}
    prv: dict[str, float] = {}
    seen_div: dict[str, str] = {}

    for item in payload.get("list", []):
        sj = item.get("sj_div")
        if sj not in base.IS_SJ_DIVS:
            continue
        for account, (ids, names) in IS_ACCOUNTS.items():
            if not _match(item, ids, names):
                continue
            # 손익계산서(IS)를 포괄손익계산서(CIS)보다 우선한다
            if account in seen_div and seen_div[account] == "IS" and sj != "IS":
                continue
            c = to_num(item.get("thstrm_add_amount"))
            if c is None:
                c = to_num(item.get("thstrm_amount"))
            p = to_num(item.get("frmtrm_add_amount"))
            if p is None:
                p = to_num(item.get("frmtrm_amount"))
            if c is not None:
                cur[account] = c
            if p is not None:
                prv[account] = p
            seen_div[account] = sj
            break

    out = {}
    if cur:
        out[(year, quarter)] = cur
    if prv:
        out[(year - 1, quarter)] = prv
    return out


def cumulative_to_quarterly(cums: dict) -> dict:
    """누계 {(년,분기): {계정: 값}} -> 분기 단독 {(년,분기): {계정: 값}}."""
    out: dict[tuple[int, int], dict[str, float]] = {}
    for (year, q), accounts in cums.items():
        prev = cums.get((year, q - 1)) if q > 1 else None
        for account, val in accounts.items():
            if q == 1:
                out.setdefault((year, q), {})[account] = val
            elif prev is not None and account in prev:
                out.setdefault((year, q), {})[account] = val - prev[account]
            # 직전 분기 누계가 없으면 분기 단독을 만들 수 없다 -> 건너뛴다
    return out


def parse_balance_accounts(payload: dict) -> dict:
    """재무상태표에서 자기자본 관련 + 추가 계정을 뽑는다."""
    res = base.parse_balance_sheet(payload)
    out = {}
    if res.equity_total is not None:
        out["자본총계"] = res.equity_total
    if res.parent_equity is not None:
        out["지배주주지분"] = res.parent_equity
    for account, (ids, names) in BS_ACCOUNTS.items():
        for item in payload.get("list", []):
            if item.get("sj_div") != base.BS_SJ_DIV:
                continue
            if _match(item, ids, names):
                val = to_num(item.get("thstrm_amount"))
                if val is not None:
                    out[account] = val
                break
    return out


def collect_one(client: DartClient, corp_code: str, periods: list) -> dict:
    """한 종목의 분기 손익 시계열 + 최신 재무상태표를 모은다."""
    cums: dict = {}
    balance: dict = {}
    balance_period = ""
    fs_used = ""
    notes: list[str] = []

    for period in periods:
        quarter = QUARTER_OF_REPRT.get(period.reprt_code)
        if quarter is None:
            continue
        payload = None
        for fs_div in (["CFS", "OFS"] if fs_used != "OFS" else ["OFS", "CFS"]):
            got = client.get_json("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(period.year),
                "reprt_code": period.reprt_code,
                "fs_div": fs_div,
            })
            if got is None:
                notes.append(f"{period.label}/{fs_div} 호출실패")
                continue
            status = got.get("status")
            if status in DART_FATAL_STATUS:
                raise SystemExit(
                    f"DART 오류 {status}: {DART_FATAL_STATUS[status]} — 실행을 중단합니다.")
            if status == "000":
                payload, fs_used = got, fs_used or fs_div
                break
            notes.append(f"{period.label}/{fs_div} 없음({status})")
        if payload is None:
            continue

        for key, vals in parse_cumulative_is(payload, period.year, quarter).items():
            cums.setdefault(key, {}).update(
                {k: v for k, v in vals.items() if k not in cums.get(key, {})})

        if not balance:      # 가장 최신 보고서의 재무상태표를 쓴다
            found = parse_balance_accounts(payload)
            if found:
                balance, balance_period = found, period.label

    return {
        "quarters": {f"{y}Q{q}": v for (y, q), v in
                     sorted(cumulative_to_quarterly(cums).items(), reverse=True)},
        "balance": balance,
        "balance_period": balance_period,
        "fs_div": "연결" if fs_used == "CFS" else ("별도" if fs_used == "OFS" else ""),
        "notes": "; ".join(notes[:4]),
    }


def collect(base_date: str = "") -> str:
    """전 대상 종목의 원천 데이터를 모아 JSON으로 저장하고 경로를 돌려준다."""
    api_key = get_api_key()
    resolved = resolve_base_date(base_date or base.BASE_DATE)
    market, resolved, source = fetch_market_snapshot(resolved)

    cand = market[market["시가총액"] >= MIN_MARKET_CAP_KRW].copy()
    cand = cand[~cand["종목명"].map(base.is_preferred_name)]
    log(f"[수집] 대상 {len(cand)}종목 (시총 {MIN_MARKET_CAP_KRW / EOK:,.0f}억 이상, "
        f"기준일 {resolved}, source={source})")

    corp_map = fetch_corp_code_map(api_key)
    periods = build_period_candidates(resolved)[:REPORTS_PER_TICKER]
    log(f"      조회 보고서: {', '.join(p.label for p in periods)}")

    client = DartClient(api_key)
    rows = [r for _, r in cand.iterrows()]

    def work(row):
        corp_code = corp_map.get(row["종목코드"])
        rec = {
            "code": row["종목코드"], "name": row["종목명"],
            "price": float(row["종가"]), "mcap": float(row["시가총액"]),
            "shares": float(row["상장주식수"]),
            "quarters": {}, "balance": {}, "balance_period": "", "fs_div": "",
            "notes": "" if corp_code else "DART corp_code 없음",
        }
        if corp_code:
            rec.update(collect_one(client, corp_code, periods))
        return rec

    records = []
    bar = tqdm(total=len(rows), desc="DART 수집", unit="종목")
    try:
        with ThreadPoolExecutor(max_workers=max(1, DART_WORKERS)) as pool:
            futures = [pool.submit(work, r) for r in rows]
            try:
                for fut in as_completed(futures):
                    records.append(fut.result())
                    bar.update(1)
            except (SystemExit, KeyboardInterrupt):
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    finally:
        bar.close()

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"fundamentals_{resolved}.json")
    payload = {
        "base_date": resolved,
        "market_source": source,
        "collected_reports": [p.label for p in periods],
        "records": sorted(records, key=lambda r: -r["mcap"]),
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)

    with_q = sum(1 for r in records if r["quarters"])
    log(f"[수집] 완료 — {len(records)}종목 (분기 데이터 확보 {with_q}) / "
        f"DART 호출 {client.calls}건 -> {path}")
    return path


# =============================================================================
# 계산: 저장된 데이터 + 등록된 지표 -> 표
# =============================================================================

def load_latest(path: str = "") -> dict:
    if path:
        target = path
    else:
        files = sorted(glob.glob(os.path.join(DATA_DIR, "fundamentals_*.json")))
        if not files:
            raise SystemExit(
                f"{DATA_DIR}/ 에 수집 데이터가 없습니다. 먼저 collect 를 실행하세요.")
        target = files[-1]
    with open(target, encoding="utf-8") as fp:
        data = json.load(fp)
    data["_path"] = target
    return data


def load_custom_metrics() -> None:
    """metrics_custom.py 가 있으면 읽어 사용자 지표를 등록한다."""
    sys.path.insert(0, os.getcwd())
    try:
        import importlib
        mod = importlib.import_module(CUSTOM_METRICS_MODULE)
        importlib.reload(mod)
        log(f"[지표] {CUSTOM_METRICS_MODULE}.py 로드")
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        log(f"[지표] [warn] {CUSTOM_METRICS_MODULE}.py 로드 실패: "
            f"{type(exc).__name__}: {exc}")


def quarter_labels(records: list, limit: int = QUARTERS) -> list:
    """전 종목에 등장하는 분기 라벨을 최신순으로 모은다."""
    seen = set()
    for rec in records:
        seen.update(rec.get("quarters", {}).keys())

    def sort_key(label):
        year, q = label.split("Q")
        return (int(year), int(q))

    return sorted(seen, key=sort_key, reverse=True)[:limit]


def build_table(data: dict) -> tuple[list, list, list]:
    """반환: (지표 목록, 분기 라벨, 행 목록)"""
    labels = quarter_labels(data["records"])
    metrics = list(METRICS.values())
    rows = []
    for rec in data["records"]:
        ctx = MetricContext(rec, labels)
        values = {}
        for m in metrics:
            try:
                val = m.fn(ctx)
            except Exception:
                val = None
            values[m.key] = (None if val is None or
                             (isinstance(val, float) and not math.isfinite(val))
                             else float(val))
        rows.append({
            "code": rec["code"], "name": rec["name"],
            "price": rec.get("price"), "mcap": rec.get("mcap"),
            "fs_div": rec.get("fs_div", ""),
            "period": rec.get("balance_period", ""),
            "notes": rec.get("notes", ""),
            "metrics": values,
            "series": {
                "매출액": [rec.get("quarters", {}).get(lb, {}).get("매출액") for lb in labels],
                "영업이익": [rec.get("quarters", {}).get(lb, {}).get("영업이익") for lb in labels],
            },
        })
    return metrics, labels, rows


# =============================================================================
# 렌더: 자체 완결형 HTML 한 장
# =============================================================================
# 외부 CDN·폰트·스크립트를 쓰지 않는다. 파일 하나만 열면 어디서든 동작하고,
# 사내 공유나 정적 호스팅에 그대로 올릴 수 있다.

PAGE_CSS = """
:root{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;--head:#f7f8fa;
--pos:#0a7c3f;--neg:#c02626;--accent:#1a56db;--chip:#eef2ff;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ac;
--line:#262a31;--head:#171a20;--pos:#3ddc84;--neg:#ff6b6b;--accent:#7aa2ff;--chip:#1c2333;}}
:root[data-theme=dark]{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ac;--line:#262a31;
--head:#171a20;--pos:#3ddc84;--neg:#ff6b6b;--accent:#7aa2ff;--chip:#1c2333;}
:root[data-theme=light]{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;
--head:#f7f8fa;--pos:#0a7c3f;--neg:#c02626;--accent:#1a56db;--chip:#eef2ff;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:100%;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px}
.sub code{background:var(--chip);padding:1px 5px;border-radius:4px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
input[type=search],select{font:inherit;padding:6px 10px;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--fg);min-width:0}
input[type=search]{flex:1 1 220px}
button{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--accent)}
details{border:1px solid var(--line);border-radius:6px;margin-bottom:14px}
summary{padding:8px 12px;cursor:pointer;font-weight:600}
.cols{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 12px}
.cols label{display:inline-flex;align-items:center;gap:5px;background:var(--chip);
padding:4px 9px;border-radius:999px;font-size:12px;cursor:pointer}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}
th{background:var(--head);position:sticky;top:0;cursor:pointer;user-select:none;font-size:12px;
text-align:right;z-index:2}
th:hover{color:var(--accent)}
th.s-asc::after{content:" \\2191";color:var(--accent)}
th.s-desc::after{content:" \\2193";color:var(--accent)}
td.t,th.t{text-align:left}
tbody tr:hover{background:var(--head)}
.name{font-weight:600}
.code{color:var(--muted);font-size:12px;margin-right:6px}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.na{color:var(--muted)}
.spark{display:block}
.foot{color:var(--muted);font-size:12px;margin-top:12px}
.count{color:var(--muted);font-size:13px}
@media (max-width:640px){.wrap{padding:14px 10px 40px}th,td{padding:6px 8px}}
"""

PAGE_JS = r"""
var RAW = __DATA__;
var tbody = document.getElementById('rows');
var q = document.getElementById('q');
var sortKey = '__mcap', sortDir = -1;
var hidden = {};

function fmtNum(v, spec){
  if (v === null || v === undefined) return null;
  var plus = spec.indexOf('+') >= 0;
  var m = spec.match(/\.(\d)f/); var d = m ? +m[1] : 2;
  var s = Math.abs(v).toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d});
  return (v < 0 ? '-' : (plus ? '+' : '')) + s;
}
function eok(v){
  if (v === null || v === undefined) return '';
  return Math.round(v/1e8).toLocaleString('ko-KR');
}
function spark(vals){
  var pts = vals.slice().reverse();            // 오래된 -> 최신
  var ok = pts.filter(function(v){return v !== null && v !== undefined;});
  if (ok.length < 2) return '';
  var w = 88, h = 22, lo = Math.min.apply(null, ok), hi = Math.max.apply(null, ok);
  if (lo > 0) lo = 0;                          // 0 기준선을 항상 보이게
  var span = (hi - lo) || 1, step = w / (pts.length - 1), d = '', started = false;
  for (var i = 0; i < pts.length; i++){
    var v = pts[i]; if (v === null || v === undefined) { started = false; continue; }
    var x = i * step, y = h - (v - lo) / span * h;
    d += (started ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    started = true;
  }
  var zero = h - (0 - lo) / span * h;
  var last = ok[ok.length - 1], color = last < 0 ? 'var(--neg)' : 'var(--pos)';
  return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h +
    '" aria-hidden="true"><line x1="0" y1="' + zero.toFixed(1) + '" x2="' + w + '" y2="' +
    zero.toFixed(1) + '" stroke="var(--line)" stroke-width="1"/><path d="' + d +
    '" fill="none" stroke="' + color + '" stroke-width="1.6" stroke-linejoin="round"/></svg>';
}
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

function render(){
  var term = q.value.trim().toLowerCase();
  var rows = RAW.rows.filter(function(r){
    return !term || r.name.toLowerCase().indexOf(term) >= 0 || r.code.indexOf(term) >= 0;
  });
  rows.sort(function(a, b){
    var x, y;
    if (sortKey === '__mcap'){ x = a.mcap; y = b.mcap; }
    else if (sortKey === '__name'){ return a.name.localeCompare(b.name, 'ko') * sortDir; }
    else { x = a.metrics[sortKey]; y = b.metrics[sortKey]; }
    var xn = (x === null || x === undefined), yn = (y === null || y === undefined);
    if (xn && yn) return 0;
    if (xn) return 1;                          // 값 없는 행은 항상 아래로
    if (yn) return -1;
    return (x - y) * sortDir;
  });

  var out = [];
  for (var i = 0; i < rows.length; i++){
    var r = rows[i], td = [];
    td.push('<td class="t"><span class="code">' + r.code + '</span><span class="name">' +
            esc(r.name) + '</span></td>');
    if (!hidden.__price) td.push('<td>' + (r.price ? r.price.toLocaleString('ko-KR') : '') + '</td>');
    if (!hidden.__mcap) td.push('<td>' + eok(r.mcap) + '</td>');
    for (var j = 0; j < RAW.metrics.length; j++){
      var m = RAW.metrics[j];
      if (hidden[m.key]) continue;
      var v = r.metrics[m.key], s = fmtNum(v, m.fmt);
      var cls = '';
      if (s === null) cls = 'na';
      else if (m.better === 'high') cls = v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
      else if (m.better === 'low' && v < 0) cls = 'neg';
      td.push('<td class="' + cls + '">' + (s === null ? '–' : s) + '</td>');
    }
    if (!hidden.__spark){
      td.push('<td>' + spark(r.series['매출액']) + '</td>');
      td.push('<td>' + spark(r.series['영업이익']) + '</td>');
    }
    if (!hidden.__period) td.push('<td class="t na">' + esc(r.period || '') +
                                  (r.fs_div ? ' ' + esc(r.fs_div) : '') + '</td>');
    out.push('<tr>' + td.join('') + '</tr>');
  }
  tbody.innerHTML = out.join('');
  document.getElementById('count').textContent = rows.length + ' / ' + RAW.rows.length + '종목';
  document.querySelectorAll('th[data-key]').forEach(function(th){
    th.className = th.className.replace(/\s*s-(asc|desc)/g, '');
    if (th.dataset.key === sortKey) th.className += sortDir > 0 ? ' s-asc' : ' s-desc';
  });
}

document.querySelectorAll('th[data-key]').forEach(function(th){
  th.addEventListener('click', function(){
    var k = th.dataset.key;
    if (sortKey === k) sortDir = -sortDir;
    else { sortKey = k; sortDir = (k === '__name') ? 1 : (th.dataset.better === 'high' ? -1 : 1); }
    render();
  });
});
document.querySelectorAll('.cols input').forEach(function(cb){
  cb.addEventListener('change', function(){
    hidden[cb.dataset.key] = !cb.checked;
    document.querySelectorAll('[data-col="' + cb.dataset.key + '"]').forEach(function(el){
      el.style.display = cb.checked ? '' : 'none';
    });
    render();
  });
});
// default_on=false 로 등록된 지표는 처음부터 숨긴 상태로 맞춘다
document.querySelectorAll('.cols input').forEach(function(cb){
  if (cb.checked) return;
  hidden[cb.dataset.key] = true;
  document.querySelectorAll('[data-col="' + cb.dataset.key + '"]').forEach(function(el){
    el.style.display = 'none';
  });
});
q.addEventListener('input', render);
document.getElementById('csv').addEventListener('click', function(){
  var cols = ['종목코드','종목명','종가','시가총액(억)'];
  RAW.metrics.forEach(function(m){ if (!hidden[m.key]) cols.push(m.key); });
  var lines = [cols.join(',')];
  RAW.rows.forEach(function(r){
    var cells = [r.code, '"' + r.name + '"', r.price, Math.round(r.mcap/1e8)];
    RAW.metrics.forEach(function(m){
      if (hidden[m.key]) return;
      var v = r.metrics[m.key];
      cells.push(v === null || v === undefined ? '' : v);
    });
    lines.push(cells.join(','));
  });
  var blob = new Blob(['﻿' + lines.join('\n')], {type:'text/csv;charset=utf-8'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dashboard_' + RAW.base_date + '.csv';
  a.click();
});
render();
"""


def render_html(data: dict, out_path: str = OUTPUT_HTML) -> str:
    metrics, labels, rows = build_table(data)

    payload = {
        "base_date": data.get("base_date", ""),
        "metrics": [{"key": m.key, "fmt": m.fmt, "better": m.better} for m in metrics],
        "rows": rows,
    }

    # 열 표시/숨김 칩. default_on=False 인 지표는 처음엔 꺼진 채로 둔다.
    chips = [('__price', '종가', True), ('__mcap', '시가총액', True)]
    chips += [(m.key, m.key, m.default_on) for m in metrics]
    chips += [('__spark', '분기 추이', True), ('__period', '기준', True)]
    chip_html = "".join(
        f'<label><input type="checkbox" data-key="{html.escape(k)}"'
        f'{" checked" if on else ""}> {html.escape(t)}</label>'
        for k, t, on in chips)

    head = ['<th class="t" data-key="__name">종목</th>',
            '<th data-col="__price" data-key="__price">종가</th>',
            '<th data-col="__mcap" data-key="__mcap">시총(억)</th>']
    for m in metrics:
        tip = html.escape(m.desc)
        head.append(f'<th data-col="{html.escape(m.key)}" data-key="{html.escape(m.key)}" '
                    f'data-better="{m.better}" title="{tip}">{html.escape(m.key)}</th>')
    head.append('<th data-col="__spark">매출 추이</th>')
    head.append('<th data-col="__spark">영업이익 추이</th>')
    head.append('<th class="t" data-col="__period">기준</th>')

    reports = ", ".join(data.get("collected_reports", []))
    quarters = " · ".join(reversed(labels))
    body = f"""<div class="wrap">
<h1>코스피 분기 실적 대시보드</h1>
<div class="sub">기준일 <code>{html.escape(data.get('base_date',''))}</code> ·
{len(rows)}종목 · 분기 {html.escape(quarters)} ·
수집 보고서 {html.escape(reports)}</div>

<div class="bar">
  <input type="search" id="q" placeholder="종목명 또는 종목코드 검색" aria-label="검색">
  <span class="count" id="count"></span>
  <button id="csv" type="button">CSV 저장</button>
</div>

<details><summary>표시할 열 고르기</summary><div class="cols">{chip_html}</div></details>

<div class="scroll"><table>
<thead><tr>{''.join(head)}</tr></thead>
<tbody id="rows"></tbody>
</table></div>

<div class="foot">
숫자 열 머리글을 누르면 정렬됩니다. 값이 없는 종목(<span class="na">–</span>)은 항상 아래로 내려갑니다.<br>
PER은 최근 4분기 지배주주순이익이 적자면 계산하지 않습니다. PBR·ROE의 자기자본은
지배주주지분 우선입니다. 금융지주·은행·보험은 자본 구조가 달라 PBR·PER을
제조업과 같은 기준으로 비교하면 안 됩니다.
</div>
</div>"""

    script = PAGE_JS.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    doc = (f"<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<title>코스피 분기 실적 대시보드</title><style>{PAGE_CSS}</style></head>"
           f"<body>{body}<script>{script}</script></body></html>")

    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    log(f"[렌더] {out_path} — {len(rows)}종목 × 지표 {len(metrics)}개 "
        f"({', '.join(m.key for m in metrics)})")
    return out_path


# =============================================================================
# CLI
# =============================================================================

def main(argv: list = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = next((a for a in argv if not a.startswith("-")), "all")

    if cmd not in ("collect", "render", "all"):
        raise SystemExit(f"알 수 없는 명령: {cmd} (collect | render | all)")

    if cmd in ("collect", "all"):
        collect()
    load_custom_metrics()
    data = load_latest()
    log(f"[렌더] 데이터 {data['_path']} (수집일 {data.get('base_date')})")
    render_html(data)


if __name__ == "__main__":
    main()
