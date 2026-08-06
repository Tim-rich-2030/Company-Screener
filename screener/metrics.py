# -*- coding: utf-8 -*-
"""
분기별 지표 계산.

핵심은 QuarterContext 다. 이 객체는 quarterly_dashboard.MetricContext 와 **완전히 같은
인터페이스**(c.mcap / c.equity / c.q / c.ttm / c.yoy ...)를 갖되, 기준점이 '최신'이
아니라 '지정한 분기'다. 덕분에 이미 등록된 지표 함수를 한 줄도 고치지 않고 과거
아무 분기에나 그대로 돌릴 수 있다. 지표를 하나 추가하면 전 분기 시계열이 함께 생긴다.

    2023Q4 시점의 PBR = 2023-12-29 시총 / 2023Q4 지배주주지분

시가총액은 그 분기말 종가 × 그 시점 주식수다 (prices.py 참고). 오늘 주가로 과거
PBR을 계산하면 시계열이 아니라 착시가 된다.
"""
from __future__ import annotations

import quarterly_dashboard as qd
from quarterly_dashboard import metric      # noqa: F401  (지표 추가용 재수출)

from .store import sort_quarters

EOK = 100_000_000


class QuarterContext:
    """특정 종목의 특정 분기 시점에서 본 데이터."""

    def __init__(self, record: dict, quarters: list, index: int):
        self._q = record.get("quarters", {})
        self._order = quarters[index:]      # 이 분기부터 과거로
        self._slot = self._q.get(quarters[index], {})
        self.code = record.get("code", "")
        self.name = record.get("name", "")
        self.qkey = quarters[index]
        # 없으면 0 이 아니라 None. 0 으로 두면 PBR·PER 이 "0.00" 으로 찍혀
        # 마치 계산된 값처럼 보인다. None 이면 지표가 비고 정렬에서 뒤로 간다.
        self.price = self._slot.get("종가")
        self.shares = self._slot.get("상장주식수")
        # 재무제표를 USD로 내는 회사(예: 두산밥캣)가 있다. 달러 자기자본을 원화
        # 시가총액으로 나누면 PBR이 1,000배로 나온다. 통화가 원화가 아니면
        # 시가총액 기반 지표(PBR·PER·PSR)를 계산하지 않는다.
        # ROE·영업이익률처럼 같은 통화끼리의 비율은 통화와 무관하므로 그대로 둔다 —
        # 단, 손익과 재무상태표가 "같은" 통화일 때만이다. 두산밥캣 2023년
        # 1~3분기는 손익은 달러인데 재무상태표(자본총계 등)는 원화 그대로 남아
        # 있었다. bs_currency 가 currency 와 다르면 그 분기의 재무상태표 값은
        # 못 믿는다 — ROE·ROA가 자릿수 3개가 빠진 값으로 조용히 찍힌다.
        self.currency = (self._slot.get("currency") or "KRW").upper()
        self.bs_currency = (self._slot.get("bs_currency") or self.currency).upper()
        self.mcap = self._slot.get("시가총액") if self.currency == "KRW" else None

    # --- 재무상태표 (이 분기 시점의 잔액) ---
    @property
    def equity(self):
        if self.bs_currency != self.currency:
            return None
        return self._slot.get("지배주주지분") or self._slot.get("자본총계")

    def bs(self, account: str):
        if self.bs_currency != self.currency:
            return None
        return self._slot.get(account)

    # --- 손익 시계열 (이 분기 기준 과거 방향) ---
    def q(self, account: str, n: int = 0):
        if n >= len(self._order):
            return None
        return self._q.get(self._order[n], {}).get(account)

    def series(self, account: str, n: int = 0):
        labels = self._order[:n] if n else self._order
        return [self._q.get(lb, {}).get(account) for lb in labels]

    def ttm(self, account: str):
        vals = self.series(account, 4)
        if len(vals) < 4 or any(v is None for v in vals):
            return None
        return sum(vals)

    def ttm_prev(self, account: str):
        vals = self.series(account)[4:8]
        if len(vals) < 4 or any(v is None for v in vals):
            return None
        return sum(vals)

    def yoy(self, account: str):
        cur, prev = self.q(account, 0), self.q(account, 4)
        if cur is None or prev is None or prev == 0:
            return None
        return (cur - prev) / abs(prev) * 100


# =============================================================================
# 분기 시계열용 추가 지표 — 대시보드와 같은 레지스트리를 쓴다
# =============================================================================

@metric("매출액(억)", desc="해당 분기 매출액", fmt="{:.0f}", group="실적")
def m_revenue(c):
    v = c.q("매출액")
    return v / EOK if v is not None else None


@metric("영업이익(억)", desc="해당 분기 영업이익", fmt="{:.0f}",
        better="high", group="실적")
def m_op(c):
    v = c.q("영업이익")
    return v / EOK if v is not None else None


@metric("순이익(억)", desc="해당 분기 지배주주순이익", fmt="{:.0f}",
        better="high", group="실적")
def m_ni(c):
    v = c.q("지배주주순이익")
    if v is None:
        v = c.q("순이익")
    return v / EOK if v is not None else None


@metric("매출 YoY(%)", desc="해당 분기 매출액의 전년 동기 대비 증감률",
        fmt="{:+.1f}", better="high", group="성장성")
def m_rev_yoy(c):
    return c.yoy("매출액")


@metric("분기 영업이익률(%)", desc="해당 분기 영업이익 / 그 분기 매출액 × 100",
        fmt="{:.1f}", better="high", group="수익성")
def m_q_opm(c):
    op, rev = c.q("영업이익"), c.q("매출액")
    return op / rev * 100 if op is not None and rev else None


@metric("ROA(%)", desc="최근 4분기 순이익 / 자산총계 × 100",
        fmt="{:.1f}", better="high", group="수익성")
def m_roa(c):
    ni = c.ttm("순이익") or c.ttm("지배주주순이익")
    assets = c.bs("자산총계")
    return ni / assets * 100 if ni is not None and assets else None


def compute_timeseries(record: dict, metrics: dict = None) -> dict:
    """
    한 종목의 분기별 지표 값을 계산한다.
    반환: {"quarters": [최신순 라벨], "metrics": {지표명: [분기별 값]}}
    """
    metrics = metrics or qd.METRICS
    quarters = sort_quarters(record.get("quarters", {}))
    out = {key: [] for key in metrics}
    for i in range(len(quarters)):
        ctx = QuarterContext(record, quarters, i)
        for key, m in metrics.items():
            try:
                val = m.fn(ctx)
            except Exception:
                val = None
            if val is not None:
                try:
                    val = round(float(val), 4)
                    if val != val or val in (float("inf"), float("-inf")):
                        val = None
                except (TypeError, ValueError):
                    val = None
            out[key].append(val)
    return {"quarters": quarters, "metrics": out}
