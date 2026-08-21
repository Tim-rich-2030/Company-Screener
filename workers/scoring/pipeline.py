"""Scoring 파이프라인: metrics → scores → freshness gate → snapshot → lifecycle.

deterministic — Claude 미개입 (명세 §2.1). 실행은 workflow 'score-and-rank'로 기록된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..lib import config, db
from ..lib.timeutil import minutes_between
from . import formulas as F
from . import freshness

PRECISE = ("SECOND", "MINUTE")


def _mentions(conn, term_id):
    return db.all_rows(
        conn,
        """
        select tm.effective_at, tm.published_precision, tm.source_type,
               si.content_hash, s.name as source_name, s.official_source
        from term_mentions tm
        join source_items si on si.id = tm.source_item_id
        join sources s on s.source_id = si.source_id
        where tm.term_id = %s
        """,
        (term_id,),
    )


def _count_precise(mentions, start, end) -> int:
    """6h급 윈도우: SECOND/MINUTE만 (docs/DATABASE.md §2 — 정밀도 혼합 금지)."""
    return len({m["content_hash"] for m in mentions
                if m["published_precision"] in PRECISE and start <= m["effective_at"] < end})


def _count_all(mentions, start, end) -> int:
    """24h+ 윈도우: 전체 precision 포함."""
    return len({m["content_hash"] for m in mentions if start <= m["effective_at"] < end})


def compute_candidate_metrics(conn, cand: dict, now: datetime, cfg: dict,
                              demand: dict | None) -> dict:
    mentions = _mentions(conn, cand["primary_term_id"])
    h = timedelta(hours=1)

    cur_6h = _count_precise(mentions, now - 6 * h, now)
    prev_6h = _count_precise(mentions, now - 12 * h, now - 6 * h)
    prev_prev_6h = _count_precise(mentions, now - 18 * h, now - 12 * h)

    # 7-day same-hour baseline (명세 §25): 지난 7일 동일 6h 창의 평균 (정밀 데이터만)
    baseline_counts = [
        _count_precise(mentions, now - timedelta(days=d, hours=6), now - timedelta(days=d))
        for d in range(1, 8)
    ]
    baseline_6h = sum(baseline_counts) / len(baseline_counts)

    docs_24h_all = _count_all(mentions, now - 24 * h, now)
    mentions_24h = len([m for m in mentions if now - 24 * h <= m["effective_at"] < now])

    # novelty: 최근 30일 중 직전 24h를 제외한 언급 (명세 §28)
    m30_before = len([m for m in mentions
                      if now - timedelta(days=30) <= m["effective_at"] < now - 24 * h])

    # cross-source: 유니크 문서(hash)당 첫 소스 타입 → distinct 소스 클러스터 (명세 §29)
    recent = [m for m in mentions if m["effective_at"] >= now - 24 * h]
    first_type_by_hash: dict[str, str] = {}
    for m in sorted(recent, key=lambda x: x["effective_at"]):
        first_type_by_hash.setdefault(m["content_hash"], m["source_type"])
    distinct_sources = len(set(first_type_by_hash.values()))

    # content supply (명세 §32): blog/cafe는 DAY/UNKNOWN이어도 일 단위 공급 집계에 사용
    supply = {
        "blog_24h": len({m["content_hash"] for m in mentions
                         if m["source_type"] == "blog" and m["effective_at"] >= now - 24 * h}),
        "blog_7d": len({m["content_hash"] for m in mentions
                        if m["source_type"] == "blog"
                        and m["effective_at"] >= now - timedelta(days=7)}),
        "cafe_24h": len({m["content_hash"] for m in mentions
                         if m["source_type"] == "cafe" and m["effective_at"] >= now - 24 * h}),
        "cafe_7d": len({m["content_hash"] for m in mentions
                        if m["source_type"] == "cafe"
                        and m["effective_at"] >= now - timedelta(days=7)}),
    }

    vel = F.velocity(cur_6h, baseline_6h, cur_6h, cfg)
    acc = F.acceleration(cur_6h, prev_6h, prev_prev_6h, cfg)
    nov = F.novelty(m30_before, cfg)

    precise_recent = [m for m in mentions if m["published_precision"] in PRECISE]
    newest_age = (min(minutes_between(now, m["effective_at"]) for m in precise_recent) / 60.0
                  if precise_recent else None)

    d = (demand or {}).get(cand["cluster_name"], {})
    if not d:
        # demand 데이터는 일 1회 갱신 — 없으면 최신 metrics의 값을 이어받는다
        prev = db.one(
            conn,
            """
            select search_trend_ratio, monthly_search from candidate_metrics
            where candidate_id = %s and (search_trend_ratio is not null
                                         or monthly_search is not null)
            order by window_end desc limit 1
            """,
            (cand["id"],),
        )
        if prev:
            d = {"search_trend_ratio": (float(prev["search_trend_ratio"])
                                        if prev["search_trend_ratio"] is not None else None),
                 "monthly_search": prev["monthly_search"]}
    google_trend_active = any(m["source_type"] == "trend"
                              and m["effective_at"] >= now - 24 * h for m in mentions)
    official = any(m["official_source"] for m in recent)
    days_covered_14d = len({m["effective_at"].date() for m in mentions
                            if m["effective_at"] >= now - timedelta(days=14)})

    metrics = {
        "cur_6h": cur_6h, "prev_6h": prev_6h, "prev_prev_6h": prev_prev_6h,
        "baseline_6h": baseline_6h, "mentions_24h": mentions_24h,
        "docs_24h": docs_24h_all, "distinct_sources": distinct_sources,
        "velocity": vel, "acceleration": acc, "novelty": nov,
        "supply": supply, "newest_precise_age_h": newest_age,
        "google_trend_active": google_trend_active, "official_evidence": official,
        "days_covered_14d": days_covered_14d,
        "search_trend_ratio": d.get("search_trend_ratio"),
        "monthly_search": d.get("monthly_search"),
        "evidence_count": len({m["content_hash"] for m in mentions}),
    }

    db.execute(
        conn,
        """
        insert into candidate_metrics
          (candidate_id, window_start, window_end, mentions, distinct_documents,
           distinct_sources, velocity, acceleration, novelty, search_trend_ratio,
           monthly_search, content_supply)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (candidate_id, window_start, window_end) do update set
          mentions = excluded.mentions,
          distinct_documents = excluded.distinct_documents,
          distinct_sources = excluded.distinct_sources,
          velocity = excluded.velocity,
          acceleration = excluded.acceleration,
          novelty = excluded.novelty,
          search_trend_ratio = excluded.search_trend_ratio,
          monthly_search = excluded.monthly_search,
          content_supply = excluded.content_supply
        """,
        (cand["id"], now - 6 * h, now, mentions_24h, cur_6h, distinct_sources,
         round(vel, 3), round(acc, 3), round(nov, 1),
         metrics["search_trend_ratio"], metrics["monthly_search"], json.dumps(supply)),
    )
    return metrics


def _reasons(m: dict, statuses: dict) -> tuple[list[str], list[str]]:
    """추천/비추천 사유 — deterministic facts로만 생성 (명세 §40). Claude 미사용."""
    reasons, risks = [], []
    if m["velocity"] > 1:
        reasons.append(f"최근 6h 언급 {m['velocity']:.1f}배 (시간정밀 문서 {m['cur_6h']}건, baseline {m['baseline_6h']:.1f})")
    if m["acceleration"] > 1:
        reasons.append(f"증가 가속 {m['acceleration']:.1f}x")
    if m["distinct_sources"] >= 2:
        reasons.append(f"소스 {m['distinct_sources']}종에서 동시 등장 (24h 문서 {m['docs_24h']}건)")
    if m["google_trend_active"]:
        reasons.append("Google Trends 활성 트렌드에 포함")
    if m["search_trend_ratio"]:
        reasons.append(f"네이버 검색추세 ratio {m['search_trend_ratio']:.0f}")
    supply_7d = m["supply"]["blog_7d"] + m["supply"]["cafe_7d"]
    if supply_7d <= 10:
        reasons.append(f"최근 7일 블로그·카페 공급 {supply_7d}건 — 낮음")

    if m["cur_6h"] < 6:
        risks.append(f"시간정밀 표본 {m['cur_6h']}건으로 적음 — 신호가 사라질 수 있음")
    if not m["monthly_search"]:
        risks.append("월간 검색량 데이터 없음 (신규 키워드 가능성)")
    if supply_7d > 10:
        risks.append(f"최근 7일 콘텐츠 공급 이미 {supply_7d}건")
    yellow_or_red = [k for k, v in statuses.items() if v != "GREEN"]
    if yellow_or_red:
        risks.append(f"소스 상태 저하: {', '.join(sorted(yellow_or_red))}")
    return reasons[:5], risks


def score_candidate(conn, cand: dict, m: dict, statuses: dict, now: datetime,
                    cfg: dict) -> dict:
    early_components = {
        "velocity": F.velocity_norm(m["velocity"], cfg),
        "acceleration": F.acceleration_norm(m["acceleration"], cfg),
        "novelty": m["novelty"],
        "cross_source": F.cross_source_score(m["distinct_sources"], cfg),
        "google_trend": 100.0 if m["google_trend_active"] else 0.0,
        "event_freshness": F.event_freshness_score(m["newest_precise_age_h"]),
    }
    early = F.weighted(early_components, cfg["early_signal_weights"])

    opp_components = {
        "early_signal": early,
        "search_trend": m["search_trend_ratio"] or 0.0,
        "absolute_demand": F.demand_score(m["monthly_search"], cfg),
        "content_gap": F.content_gap_score(m["supply"]["blog_7d"], m["supply"]["cafe_7d"]),
        # M1 stub: blog_fit/monetization은 M2에서 intent 신호 기반으로 교체
        "blog_fit": 70.0,
        "monetization": 70.0 if cand["category"] in
            ("electronics", "camera", "home_appliance", "shopping", "mobile", "pc") else 45.0,
    }
    opportunity = F.weighted(opp_components, cfg["opportunity_weights"])

    ev_sources = {r["source_name"] for r in db.all_rows(
        conn,
        """
        select distinct s.name as source_name
        from candidate_evidence ce
        join source_items si on si.id = ce.source_item_id
        join sources s on s.source_id = si.source_id
        where ce.candidate_id = %s
        """,
        (cand["id"],),
    )}
    relevant_statuses = {n: statuses[n] for n in ev_sources if n in statuses}
    status_score = {"GREEN": 100.0, "YELLOW": 50.0, "RED": 0.0}
    conf_components = {
        "source_freshness": (sum(status_score[v] for v in relevant_statuses.values())
                             / len(relevant_statuses)) if relevant_statuses else 0.0,
        "evidence_count": F.clamp(100.0 * m["evidence_count"]
                                  / cfg["evidence"]["confidence_full_evidence_count"]),
        "cross_source": F.cross_source_score(m["distinct_sources"], cfg),
        "historical_coverage": F.clamp(100.0 * m["days_covered_14d"] / 14.0),
        "official_evidence": 100.0 if m["official_evidence"] else 0.0,
    }
    confidence = F.weighted(conf_components, cfg["confidence_weights"])

    gate = freshness.freshness_gate(cand["candidate_type"], statuses, ev_sources)
    rank = F.rank_score(opportunity, confidence, cfg)
    life = F.lifecycle_for(opportunity, confidence, gate, cfg)
    reasons, risks = _reasons(m, relevant_statuses)

    cutoff = db.one(conn, "select market_complete_through, policy_complete_through from v_data_cutoff")
    data_through = (cutoff["policy_complete_through"] if cand["candidate_type"] == "policy"
                    else cutoff["market_complete_through"])

    components = {
        "early_signal": {k: round(v, 1) for k, v in early_components.items()},
        "opportunity": {k: round(v, 1) for k, v in opp_components.items()},
        "confidence": {k: round(v, 1) for k, v in conf_components.items()},
        "weights": {
            "early_signal": cfg["early_signal_weights"],
            "opportunity": cfg["opportunity_weights"],
            "confidence": cfg["confidence_weights"],
            "rank": cfg["rank"],
        },
        "metrics": {
            "velocity": round(m["velocity"], 3), "acceleration": round(m["acceleration"], 3),
            "cur_6h": m["cur_6h"], "baseline_6h": round(m["baseline_6h"], 2),
            "docs_24h": m["docs_24h"], "distinct_sources": m["distinct_sources"],
            "supply": m["supply"], "evidence_count": m["evidence_count"],
        },
        "source_status": statuses,
        "reasons": reasons,
        "risks": risks,
    }

    db.execute(
        conn,
        """
        insert into score_snapshots
          (candidate_id, score_version, calculated_at, data_complete_through,
           early_signal, opportunity, confidence, rank_score, freshness_pass, components)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (cand["id"], cfg["score_version"], now, data_through,
         round(early, 1), round(opportunity, 1), round(confidence, 1),
         round(rank, 1), gate, json.dumps(components)),
    )
    db.execute(
        conn,
        """
        update candidates set lifecycle = %s, updated_at = %s,
          first_now_at = case when %s = 'now' and first_now_at is null
                              then %s else first_now_at end
        where id = %s
        """,
        (life, now, life, now, cand["id"]),
    )
    return {"opportunity": opportunity, "confidence": confidence,
            "rank": rank, "gate": gate, "lifecycle": life}


def run_scoring(conn, now: datetime, demand: dict | None = None,
                trigger: str = "local") -> list[dict]:
    """전체 후보 재계산. workflow_runs에 'score-and-rank'로 기록."""
    cfg = config.scoring_config()
    wr = db.one(
        conn,
        """
        insert into workflow_runs (workflow_name, trigger_type, started_at, status)
        values ('score-and-rank', %s, %s, 'running') returning id
        """,
        (trigger, now),
    )
    try:
        statuses = freshness.update_system_health(conn, now)
        cands = db.all_rows(
            conn,
            """
            select c.id, c.primary_term_id, c.cluster_name, c.candidate_type, c.category
            from candidates c
            """,
        )
        results = []
        for cand in cands:
            m = compute_candidate_metrics(conn, cand, now, cfg, demand)
            r = score_candidate(conn, cand, m, statuses, now, cfg)
            results.append({"candidate": cand["cluster_name"], **r})
        db.execute(
            conn,
            """
            update workflow_runs set status = 'success', completed_at = %s,
              duration_seconds = extract(epoch from (%s - started_at)),
              items_received = %s
            where id = %s
            """,
            (now, now, len(results), wr["id"]),
        )
        return results
    except Exception as e:
        db.execute(
            conn,
            "update workflow_runs set status = 'failed', completed_at = %s, error_message = %s where id = %s",
            (now, str(e)[:500], wr["id"]),
        )
        raise
