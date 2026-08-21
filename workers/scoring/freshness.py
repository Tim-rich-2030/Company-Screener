"""Freshness 판정 — docs/DATA_FRESHNESS.md §3, §6의 구현.

source_status / freshness_gate는 순수 함수 (unit test 대상).
update_system_health만 DB를 만진다.
"""
from __future__ import annotations

from datetime import datetime

from ..lib import db
from ..lib.timeutil import KST, minutes_between

GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"


def source_status(
    cadence: str,
    sla: dict,
    last_success_at: datetime | None,
    data_through: datetime | None,
    now: datetime,
) -> str:
    """소스 하나의 상태. fetch 성공만으로 GREEN이 되지 않는다 (명세 §61 Test C)."""
    if last_success_at is None:
        return RED

    if cadence == "realtime":
        age_min = minutes_between(now, last_success_at)
        if age_min < sla["green_lt"]:
            return GREEN
        if age_min < sla["red_gte"]:
            return YELLOW
        return RED

    if cadence == "daily":
        # 수집 자체가 밀렸는가
        if minutes_between(now, last_success_at) > 26 * 60:
            return RED
        # 데이터가 실제로 오래됐는가 — data_through(KST 날짜) 기준
        if data_through is None:
            return RED
        lag_days = (now.astimezone(KST).date() - data_through.astimezone(KST).date()).days
        expected = sla.get("expected_lag_days", 1)
        if lag_days <= expected:
            return GREEN
        if lag_days == expected + 1:
            return YELLOW
        return RED

    if cadence == "monthly":
        # Demand Base 지표 — 실시간 판정 없음. 35일 미갱신만 YELLOW.
        if minutes_between(now, last_success_at) > 35 * 24 * 60:
            return YELLOW
        return GREEN

    raise ValueError(f"unknown cadence: {cadence}")


def freshness_gate(
    candidate_type: str,
    statuses: dict[str, str],
    evidence_source_names: set[str] | None = None,
) -> bool:
    """명세 §21. YELLOW는 통과 불가 — GREEN만 인정한다."""
    ok = lambda name: statuses.get(name) == GREEN  # noqa: E731

    if candidate_type == "market":
        naver_ok = all(ok(n) for n in ("naver_news", "naver_blog", "naver_cafe"))
        return naver_ok and (ok("google_trends") or ok("youtube"))

    if candidate_type == "policy":
        policy_sources = {"policy_briefing", "law_go_kr", "lawmaking_notice"}
        relevant = policy_sources & (evidence_source_names or set())
        if not relevant:  # evidence에 정책 소스가 없으면 policy 후보 자격 없음
            return False
        return all(ok(n) for n in relevant) and ok("naver_news") and ok("naver_blog")

    if candidate_type == "evergreen":
        return (
            ok("naver_search_trend")
            and ok("naver_searchad")
            and ok("naver_blog")
            and ok("naver_cafe")
        )

    raise ValueError(f"unknown candidate_type: {candidate_type}")


# ── DB: system_health 갱신 ───────────────────────────────────────
def compute_statuses(conn, now: datetime) -> dict[str, str]:
    rows = db.all_rows(
        conn,
        """
        select s.name, s.cadence, s.freshness_sla_minutes as sla,
               ls.last_success_at, ls.source_data_through
        from sources s
        left join v_source_last_success ls on ls.source_id = s.source_id
        where s.enabled
        """,
    )
    return {
        r["name"]: source_status(
            r["cadence"], r["sla"], r["last_success_at"], r["source_data_through"], now
        )
        for r in rows
    }


def update_system_health(conn, now: datetime) -> dict[str, str]:
    statuses = compute_statuses(conn, now)
    for name, status in statuses.items():
        row = db.one(
            conn,
            """
            select ls.last_success_at, ls.source_data_through
            from sources s
            left join v_source_last_success ls on ls.source_id = s.source_id
            where s.name = %s
            """,
            (name,),
        )
        msg = None
        if status != GREEN and row and row["last_success_at"]:
            mins = int(minutes_between(now, row["last_success_at"]))
            msg = f"데이터가 {mins // 60}시간 {mins % 60}분 동안 갱신되지 않음"
        elif status != GREEN:
            msg = "성공한 수집 기록 없음"
        db.execute(
            conn,
            """
            insert into system_health (component, status, last_success_at, data_through,
                                       checked_at, message)
            values (%s,%s,%s,%s,%s,%s)
            on conflict (component) do update set
              status = excluded.status,
              last_success_at = excluded.last_success_at,
              data_through = excluded.data_through,
              checked_at = excluded.checked_at,
              message = excluded.message
            """,
            (name, status,
             row["last_success_at"] if row else None,
             row["source_data_through"] if row else None,
             now, msg),
        )
        _sync_alert(conn, name, status, msg, now)
    return statuses


def _sync_alert(conn, component: str, status: str, msg: str | None, now: datetime) -> None:
    if status == RED:
        db.execute(
            conn,
            """
            insert into alerts (severity, component, message, created_at)
            values ('red', %s, %s, %s)
            on conflict (component) where resolved_at is null do nothing
            """,
            (component, msg or "RED", now),
        )
    else:
        db.execute(
            conn,
            "update alerts set resolved_at = %s where component = %s and resolved_at is null",
            (now, component),
        )
