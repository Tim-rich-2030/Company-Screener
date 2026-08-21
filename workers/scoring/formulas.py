"""점수 공식 — 전부 순수 함수. 네트워크·DB 접근 금지.

가중치·임계값의 유일한 정의처는 config/scoring.v1.json이다.
AI는 이 공식을 변경할 수 없다 (명세 §35). 변경은 score_version 갱신으로만 한다.
"""
from __future__ import annotations

import math


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ── 명세 §26 Velocity ─────────────────────────────────────────────
def velocity(current_6h: int, baseline_6h: float, distinct_documents: int, cfg: dict) -> float:
    v = (current_6h + cfg["velocity"]["smoothing"]) / (baseline_6h + cfg["velocity"]["smoothing"])
    v = min(v, cfg["velocity"]["velocity_cap"])
    # sparse keyword 폭주 방지 (명세 §26)
    if distinct_documents < cfg["velocity"]["sparse_min_distinct_documents"]:
        v = min(v, cfg["velocity"]["sparse_velocity_cap"])
    return v


# ── 명세 §27 Acceleration ────────────────────────────────────────
def acceleration(current_6h: int, prev_6h: int, prev_prev_6h: int, cfg: dict) -> float:
    s = cfg["velocity"]["smoothing"]
    v_cur = (current_6h + s) / (prev_6h + s)
    v_prev = (prev_6h + s) / (prev_prev_6h + s)
    a = v_cur / v_prev
    return min(a, cfg["velocity"]["acceleration_cap"])


# ── 명세 §28 Novelty ─────────────────────────────────────────────
def novelty(mentions_30d_before: int, cfg: dict) -> float:
    """최근 30일(직전 24h 제외) 언급이 적을수록 100에 가깝다. deterministic."""
    full = cfg["novelty"]["max_mentions_30d_for_full"]
    zero = cfg["novelty"]["zero_novelty_mentions_30d"]
    if mentions_30d_before <= full:
        return 100.0
    if mentions_30d_before >= zero:
        return 0.0
    return clamp(100.0 * (1 - (mentions_30d_before - full) / (zero - full)))


# ── 명세 §29 Cross Source ────────────────────────────────────────
def cross_source_score(distinct_source_clusters: int, cfg: dict) -> float:
    table = cfg["cross_source"]
    if distinct_source_clusters <= 0:
        return 0.0
    key = str(min(distinct_source_clusters, 5))
    return float(table[key])


# ── 성분 정규화 (0~100) ──────────────────────────────────────────
def velocity_norm(v: float, cfg: dict) -> float:
    cap = cfg["velocity"]["velocity_cap"]
    return clamp(100.0 * (v - 1.0) / (cap - 1.0))


def acceleration_norm(a: float, cfg: dict) -> float:
    cap = cfg["velocity"]["acceleration_cap"]
    return clamp(100.0 * (a - 1.0) / (cap - 1.0))


def event_freshness_score(newest_precise_age_hours: float | None) -> float:
    """가장 최근의 시간-정밀(SECOND/MINUTE) evidence 나이. 2h 이내 100, 24h에서 0."""
    if newest_precise_age_hours is None:
        return 0.0
    if newest_precise_age_hours <= 2:
        return 100.0
    if newest_precise_age_hours >= 24:
        return 0.0
    return clamp(100.0 * (24 - newest_precise_age_hours) / 22.0)


def demand_score(monthly_search: int | None, cfg: dict) -> float:
    """명세 §31: 월검색량 logarithmic normalize. 없음(신규 키워드)은 0점이지만 제거 사유 아님."""
    if not monthly_search or monthly_search <= 0:
        return 0.0
    norm_max = cfg["demand"]["monthly_search_norm_max"]
    return clamp(100.0 * math.log10(monthly_search + 1) / math.log10(norm_max + 1))


def content_gap_score(blog_7d: int, cafe_7d: int) -> float:
    """명세 §32: 최근 신규 공급이 낮을수록 높은 점수. M1: 7일 blog+cafe 30건에서 0점."""
    pressure = min(1.0, (blog_7d + cafe_7d) / 30.0)
    return clamp(100.0 * (1.0 - pressure))


def weighted(components: dict[str, float], weights: dict[str, float]) -> float:
    """weights 합은 100이어야 한다. components 값은 0~100."""
    total_w = sum(weights.values())
    if round(total_w) != 100:
        raise ValueError(f"weights must sum to 100, got {total_w}")
    return clamp(sum(weights[k] * clamp(components[k]) for k in weights) / 100.0)


# ── 명세 §35 최종 Rank ───────────────────────────────────────────
def rank_score(opportunity: float, confidence: float, cfg: dict) -> float:
    r = cfg["rank"]
    return opportunity * (r["base"] + r["confidence_factor"] * confidence / 100.0)


# ── 명세 §36 lifecycle ───────────────────────────────────────────
def lifecycle_for(opportunity: float, confidence: float, freshness_pass: bool, cfg: dict) -> str:
    t = cfg["today_thresholds"]
    if (freshness_pass and opportunity >= t["now_opportunity_min"]
            and confidence >= t["now_confidence_min"]):
        return "now"
    if opportunity >= t["watch_opportunity_min"] or confidence >= t["watch_confidence_min"]:
        return "watch"
    return "new"
