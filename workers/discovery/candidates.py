"""Term 집계 → Candidate 생성 (명세 §23~§24).

- term 추출은 terms.py의 순수 함수를 쓰고, 여기서는 DB 반영만 한다.
- 문서 단위는 content_hash다 (신디케이션 중복을 1개 문서로 취급, 명세 §29).
- Rule A의 6h distinct documents는 시간-정밀(SECOND/MINUTE) 데이터만 센다
  (docs/DATABASE.md §2 — DAY/UNKNOWN은 6h 신호의 primary evidence가 될 수 없음).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..lib import config, db
from ..lib.timeutil import kst_midnight
from . import terms as T

PRECISE = ("SECOND", "MINUTE")
POLICY_SOURCES = {"policy_briefing", "law_go_kr", "lawmaking_notice"}


def effective_at(published_at, precision: str, first_seen_at):
    """집계 기준 시각 (docs/DATABASE.md §2)."""
    if precision in PRECISE:
        return published_at
    if precision == "DAY":
        return kst_midnight(published_at)
    return first_seen_at  # UNKNOWN


def rebuild_terms_and_mentions(conn, now: datetime) -> list[str]:
    """최근 활동 기준으로 term을 채택하고, 전체 히스토리에 mention을 기록한다.

    반환: 채택된 normalized_term 목록.
    """
    items = db.all_rows(
        conn,
        """
        select si.id, si.title, si.body_excerpt, si.published_at, si.published_precision,
               si.first_seen_at, si.content_hash, si.source_type, s.name as source_name
        from source_items si join sources s on s.source_id = si.source_id
        """,
    )
    recent_cut = now - timedelta(hours=48)

    # term → 최근 48h 문서(hash) 집합 (채택 판단용)
    term_recent_docs: dict[str, set[str]] = {}
    # term → 전체 매칭 item 목록 (mention 기록용)
    term_items: dict[str, list[dict]] = {}

    for it in items:
        eff = effective_at(it["published_at"], it["published_precision"], it["first_seen_at"])
        it["_effective_at"] = eff
        for gram in T.extract_ngrams(it["title"]):
            term_items.setdefault(gram, []).append(it)
            if eff >= recent_cut:
                term_recent_docs.setdefault(gram, set()).add(it["content_hash"])

    eligible = {t: d for t, d in term_recent_docs.items() if len(d) >= 3}
    accepted = T.dedupe_overlapping_terms(eligible)

    for term in accepted:
        matched = term_items[term]
        first_seen = min(m["_effective_at"] for m in matched)
        last_seen = max(m["_effective_at"] for m in matched)
        row = db.one(
            conn,
            """
            insert into terms (normalized_term, display_term, first_seen_at, last_seen_at)
            values (%s,%s,%s,%s)
            on conflict (normalized_term) do update set
              last_seen_at = greatest(terms.last_seen_at, excluded.last_seen_at)
            returning id
            """,
            (term, term, first_seen, last_seen),
        )
        term_id = row["id"]
        for m in matched:
            db.execute(
                conn,
                """
                insert into term_mentions
                  (term_id, source_item_id, published_at, published_precision,
                   effective_at, source_type)
                values (%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                """,
                (term_id, m["id"], m["published_at"], m["published_precision"],
                 m["_effective_at"], m["source_type"]),
            )
    return accepted


def _category_for(term: str) -> str:
    seeds = config.seeds_config()
    for root in seeds["roots"]:
        kw = T.normalize_text(root["keyword"])
        if kw and (kw in term or term in kw or any(tok in term for tok in kw.split())):
            return root["category"]
    return "tech"


def create_candidates(conn, now: datetime, cfg: dict) -> list[dict]:
    """Rule A(시장) / Rule B(공식 정책 이벤트)로 candidate 생성 (명세 §24)."""
    rules = cfg["candidate_rules"]
    created_or_existing: list[dict] = []
    term_rows = db.all_rows(conn, "select id, normalized_term from terms")

    for tr in term_rows:
        mentions = db.all_rows(
            conn,
            """
            select tm.effective_at, tm.published_precision, tm.source_type,
                   si.content_hash, s.name as source_name
            from term_mentions tm
            join source_items si on si.id = tm.source_item_id
            join sources s on s.source_id = si.source_id
            where tm.term_id = %s
            """,
            (tr["id"],),
        )
        six_h = now - timedelta(hours=6)
        day = now - timedelta(hours=24)

        precise_6h = [m for m in mentions
                      if m["published_precision"] in PRECISE and m["effective_at"] >= six_h]
        docs_6h = {m["content_hash"] for m in precise_6h}
        types_6h = {m["source_type"] for m in precise_6h}

        policy_recent = [m for m in mentions
                         if m["source_name"] in POLICY_SOURCES and m["effective_at"] >= day]

        rule = None
        ctype = "market"
        if (len(docs_6h) >= rules["rule_a_min_distinct_documents_6h"]
                and len(types_6h) >= rules["rule_a_min_source_types_6h"]):
            rule = "A"
        if policy_recent:  # Rule B: 공식 Policy Event (명세 §24)
            rule, ctype = "B", "policy"
        if rule is None:
            continue

        category = "policy" if ctype == "policy" else _category_for(tr["normalized_term"])
        cand = db.one(
            conn,
            """
            select c.id from candidates c where c.primary_term_id = %s
            """,
            (tr["id"],),
        )
        if cand is None:
            cand = db.one(
                conn,
                """
                insert into candidates
                  (primary_term_id, cluster_name, candidate_type, lifecycle,
                   category, created_rule)
                values (%s,%s,%s,'new',%s,%s)
                returning id
                """,
                (tr["id"], tr["normalized_term"], ctype, category, rule),
            )
        candidate_id = cand["id"]

        # evidence 연결 (mention 기반)
        db.execute(
            conn,
            """
            insert into candidate_evidence (candidate_id, source_item_id, evidence_type)
            select %s, tm.source_item_id,
                   case when s.source_type = 'trend' then 'trend'
                        when s.source_type = 'policy' then 'policy_event'
                        else 'mention' end
            from term_mentions tm
            join source_items si on si.id = tm.source_item_id
            join sources s on s.source_id = si.source_id
            where tm.term_id = %s
            on conflict do nothing
            """,
            (candidate_id, tr["id"]),
        )
        created_or_existing.append(
            {"candidate_id": candidate_id, "term_id": tr["id"],
             "term": tr["normalized_term"], "rule": rule, "type": ctype}
        )
    return created_or_existing
