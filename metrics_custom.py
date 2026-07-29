# -*- coding: utf-8 -*-
"""
내가 추가하는 지표
==================

이 파일에 함수를 하나 쓰면 대시보드에 열이 하나 늘어납니다.
DART를 다시 부르지 않으므로 `render`만 다시 돌리면 됩니다 (수 초).

    python quarterly_dashboard.py render

주석을 풀거나 새로 쓰기만 하면 됩니다. 값을 못 구하는 종목은 None을 돌려주세요
(화면에는 '–' 로 표시되고 정렬 시 항상 아래로 내려갑니다).


쓸 수 있는 것들 (인자 c)
------------------------
    c.code, c.name          종목코드, 종목명
    c.price                 종가(원)
    c.mcap                  시가총액(원)
    c.shares                상장주식수
    c.equity                자기자본 (지배주주지분 우선, 없으면 자본총계)
    c.bs("자산총계")         재무상태표 계정 — 자산총계 / 부채총계 / 자본총계 / 지배주주지분

    c.q("매출액")            최신 분기 값
    c.q("매출액", 4)         4분기 전(= 전년 동기) 값
    c.series("영업이익")      최신순 분기 값 목록
    c.ttm("순이익")          최근 4분기 합계 (한 분기라도 비면 None)
    c.ttm_prev("매출액")     그 직전 4분기 합계 (YoY 비교용)
    c.yoy("영업이익")         최신 분기의 전년 동기 대비 증감률(%)

    쓸 수 있는 계정: 매출액 / 영업이익 / 순이익 / 지배주주순이익
    (계정을 더 늘리려면 quarterly_dashboard.py 의 IS_ACCOUNTS 에 추가)


@metric 옵션
------------
    fmt        표시 형식.  "{:.2f}" 소수 2자리, "{:.0f}" 정수, "{:+.1f}" 부호 표시
    better     "low"  = 낮을수록 좋음 (PBR, PER)  -> 오름차순으로 먼저 정렬
               "high" = 높을수록 좋음 (ROE)       -> 내림차순으로 먼저 정렬, 양수는 초록
    group      묶음 이름 (지금은 설명용)
    default_on False 로 두면 처음에는 열이 숨겨진 채 시작합니다
"""

from quarterly_dashboard import metric


# --- 예시 1. 매출액 대비 시가총액 --------------------------------------------
# 적자 기업은 PER이 안 나오므로, 그럴 때 쓰는 보조 지표입니다.

@metric("PSR", desc="시가총액 / 최근 4분기 매출액", fmt="{:.2f}", better="low")
def psr(c):
    rev = c.ttm("매출액")
    return c.mcap / rev if rev and rev > 0 else None


# --- 예시 2. 여러 지표를 조합 --------------------------------------------------
# 저PBR인데 ROE도 낮으면 '싼 게 아니라 나쁜 기업'입니다. 둘을 같이 봅니다.

@metric("PBR/ROE", desc="PBR ÷ ROE(%). 낮을수록 '싼데 수익성도 있는' 쪽",
        fmt="{:.3f}", better="low", group="복합")
def pbr_over_roe(c):
    eq = c.equity
    ni = c.ttm("지배주주순이익") or c.ttm("순이익")
    if not eq or eq <= 0 or ni is None or ni <= 0:
        return None
    pbr = c.mcap / eq
    roe = ni / eq * 100
    return pbr / roe if roe > 0 else None


# --- 예시 3. 분기 시계열을 직접 다루기 -----------------------------------------
# 최근 4분기 영업이익이 계속 늘고 있는지 (연속 증가 분기 수).

@metric("영업이익 연속증가", desc="최신 분기부터 거슬러 올라가며 전분기보다 늘어난 분기 수",
        fmt="{:.0f}", better="high", group="성장성", default_on=False)
def op_up_streak(c):
    vals = c.series("영업이익")          # 최신순
    streak = 0
    for cur, prev in zip(vals, vals[1:]):
        if cur is None or prev is None or cur <= prev:
            break
        streak += 1
    return streak


# --- 여기서부터 직접 추가하세요 -------------------------------------------------
#
# @metric("배당수익률(%)", desc="...", fmt="{:.2f}", better="high")
# def dividend_yield(c):
#     ...
