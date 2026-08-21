"""Mock collector — RADAR_MODE=mock 전용.

외부 API key 없이 fixtures/mock/*.json을 실제 수집과 동일한 경로로 DB에 넣는다.
실제 collector와 같은 실행 증적(workflow_runs/source_runs)을 남기므로
/health·/workflows 화면과 freshness 판정이 실데이터와 동일하게 동작한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..lib import config, db
from ..lib.timeutil import kst_midnight
from ..discovery.normalize import content_hash, normalize_url, strip_markup

# 소스 → 담당 workflow (실제 구성과 동일: docs/WORKFLOWS.md §1)
WORKFLOW_OF = {
    "naver_news": "collect-market",
    "naver_blog": "collect-market",
    "naver_cafe": "collect-market",
    "google_trends": "collect-market",
    "policy_briefing": "collect-policy",
    "law_go_kr": "collect-policy",
    "lawmaking_notice": "collect-policy",
    "youtube": "collect-youtube",
    "naver_search_trend": "validate-demand",
    "shopping_insight": "validate-demand",
    "naver_searchad": "validate-demand",
}


def load_fixture_items() -> list[dict]:
    p = config.FIXTURES_DIR / "mock" / "items.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_fixture_demand() -> dict:
    p = config.FIXTURES_DIR / "mock" / "demand.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve_times(entry: dict, precision: str, now: datetime):
    """fixture의 상대 오프셋 → (published_at, first_seen_at).

    DAY: published는 해당 KST 날짜 자정. UNKNOWN: published 없음.
    first_seen은 '과거 run에서 이미 수집했던 것'을 재현하기 위해 명시 설정한다.
    """
    seen = now + timedelta(hours=entry.get("seen_offset_hours",
                                           entry.get("published_offset_hours", 0)))
    if precision == "UNKNOWN":
        return None, seen
    published = now + timedelta(hours=entry["published_offset_hours"])
    if precision == "DAY":
        published = kst_midnight(published)
    return published, seen


def run_mock_collection(conn, now: datetime) -> dict:
    """workflow별로 실제 수집과 동일한 라이프사이클을 재현한다."""
    source_rows = db.all_rows(
        conn, "select source_id, name, source_type, published_precision, cadence from sources")
    by_name = {r["name"]: r for r in source_rows}

    items = load_fixture_items()
    items_by_source: dict[str, list[dict]] = {}
    for it in items:
        items_by_source.setdefault(it["source"], []).append(it)

    stats = {"workflows": 0, "items_received": 0, "items_new": 0}
    workflows: dict[str, list[str]] = {}
    for name, wf in WORKFLOW_OF.items():
        if name in by_name:
            workflows.setdefault(wf, []).append(name)

    for wf_name, source_names in workflows.items():
        wr = db.one(
            conn,
            """
            insert into workflow_runs (workflow_name, trigger_type, scheduled_at,
                                       started_at, status)
            values (%s, 'test', %s, %s, 'running') returning id
            """,
            (wf_name, now, now),
        )
        wf_received = wf_new = 0
        for name in source_names:
            src = by_name[name]
            entries = items_by_source.get(name, [])
            sr = db.one(
                conn,
                """
                insert into source_runs (source_id, workflow_run_id, started_at, status)
                values (%s, %s, %s, 'running') returning id
                """,
                (src["source_id"], wr["id"], now),
            )
            new = 0
            for e in entries:
                published, seen = _resolve_times(e, src["published_precision"], now)
                title = strip_markup(e["title"])
                inserted = db.one(
                    conn,
                    """
                    insert into source_items
                      (source_id, external_id, canonical_url, title, body_excerpt,
                       published_at, published_precision, first_seen_at, fetched_at,
                       raw_payload, content_hash, source_type)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (source_id, external_id) do nothing
                    returning id
                    """,
                    (src["source_id"], e["external_id"], normalize_url(e.get("url")),
                     title, e.get("excerpt"),
                     published, src["published_precision"], seen, seen,
                     json.dumps(e), content_hash(e["title"], e.get("excerpt")),
                     src["source_type"]),
                )
                if inserted:
                    new += 1

            # source_data_through (docs/DATA_FRESHNESS.md §2)
            if src["cadence"] == "realtime":
                data_through = now
            elif src["cadence"] == "daily":
                data_through = kst_midnight(now - timedelta(days=1))  # D-1 데이터
            else:  # monthly — 기준월(전월)의 KST 1일
                data_through = kst_midnight(now.replace(day=1) - timedelta(days=1)).replace(day=1)

            db.execute(
                conn,
                """
                update source_runs set status = 'success', completed_at = %s,
                  http_status = 200, rows_received = %s, rows_new = %s,
                  source_data_through = %s
                where id = %s
                """,
                (now, len(entries), new, data_through, sr["id"]),
            )
            wf_received += len(entries)
            wf_new += new

        db.execute(
            conn,
            """
            update workflow_runs set status = 'success', completed_at = %s,
              duration_seconds = 1, items_received = %s, items_new = %s
            where id = %s
            """,
            (now, wf_received, wf_new, wr["id"]),
        )
        stats["workflows"] += 1
        stats["items_received"] += wf_received
        stats["items_new"] += wf_new

    return stats
