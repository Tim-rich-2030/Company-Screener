"""Source Registry sync: config/sources.yaml → sources 테이블.

sources.yaml이 유일한 정의처다. 워커 시작 시 upsert하며,
yaml에서 사라진 소스는 삭제하지 않고 enabled=false로 내린다.
"""
from __future__ import annotations

import json

from . import config, db


def sync_sources(conn) -> dict[str, str]:
    """yaml → DB upsert. {name: source_id} 매핑 반환."""
    cfg = config.sources_config()
    names = []
    for s in cfg["sources"]:
        names.append(s["name"])
        db.execute(
            conn,
            """
            insert into sources
              (name, provider, source_type, endpoint_type, cadence,
               collection_interval_minutes, freshness_sla_minutes,
               published_precision, required_for, enabled, priority,
               official_source, parser_version)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (name) do update set
              provider = excluded.provider,
              source_type = excluded.source_type,
              endpoint_type = excluded.endpoint_type,
              cadence = excluded.cadence,
              collection_interval_minutes = excluded.collection_interval_minutes,
              freshness_sla_minutes = excluded.freshness_sla_minutes,
              published_precision = excluded.published_precision,
              required_for = excluded.required_for,
              enabled = excluded.enabled,
              priority = excluded.priority,
              official_source = excluded.official_source,
              parser_version = excluded.parser_version
            """,
            (
                s["name"], s["provider"], s["source_type"], s["endpoint_type"],
                s["cadence"], s["collection_interval_minutes"],
                json.dumps(s.get("freshness_sla_minutes", {})),
                s["published_precision"],
                json.dumps(s.get("required_for", [])),
                s.get("enabled", True), s.get("priority", 100),
                s.get("official_source", False), s.get("parser_version", "v1"),
            ),
        )
    db.execute(
        conn,
        "update sources set enabled = false where name != all(%s)",
        (names,),
    )
    rows = db.all_rows(conn, "select name, source_id from sources")
    return {r["name"]: r["source_id"] for r in rows}
