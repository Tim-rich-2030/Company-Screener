"""워커 단일 진입점.

사용:
  RADAR_MODE=mock python -m workers.run mock-pipeline   # fixture 기반 전체 파이프라인
  python -m workers.run health                          # system_health 갱신만
  python -m workers.run score                           # scoring만 재실행
  python -m workers.run heartbeat                       # infra-heartbeat (M1.5 §7)
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

from .collectors import mock as mock_collector
from .discovery import candidates as discovery
from .lib import config, db, registry
from .lib.timeutil import utcnow
from .scoring import freshness, pipeline
from .tools import connectivity_check


def cmd_mock_pipeline() -> int:
    if config.radar_mode() != "mock":
        print("거부: mock-pipeline은 RADAR_MODE=mock 에서만 실행할 수 있다", file=sys.stderr)
        return 2
    now = utcnow()
    with db.connect() as conn:
        registry.sync_sources(conn)
        stats = mock_collector.run_mock_collection(conn, now)
        print(f"[collect] workflows={stats['workflows']} "
              f"received={stats['items_received']} new={stats['items_new']}")

        accepted = discovery.rebuild_terms_and_mentions(conn, now)
        print(f"[discovery] accepted terms: {len(accepted)} → {accepted}")

        cfg = config.scoring_config()
        cands = discovery.create_candidates(conn, now, cfg)
        print(f"[candidates] {len(cands)}: "
              f"{[(c['term'], c['rule'], c['type']) for c in cands]}")

        demand = mock_collector.load_fixture_demand()
        results = pipeline.run_scoring(conn, now, demand=demand, trigger="test")
        for r in sorted(results, key=lambda x: -x["rank"]):
            print(f"[score] {r['candidate']:<24} opp={r['opportunity']:5.1f} "
                  f"conf={r['confidence']:5.1f} rank={r['rank']:5.1f} "
                  f"gate={'PASS' if r['gate'] else 'FAIL'} → {r['lifecycle']}")
    print("[done] mock pipeline complete")
    return 0


def cmd_health() -> int:
    now = utcnow()
    with db.connect() as conn:
        statuses = freshness.update_system_health(conn, now)
        for name, st in sorted(statuses.items()):
            print(f"{st:<7} {name}")
    return 0


def cmd_score() -> int:
    now = utcnow()
    with db.connect() as conn:
        results = pipeline.run_scoring(conn, now, demand=None, trigger="manual")
        for r in sorted(results, key=lambda x: -x["rank"]):
            print(f"{r['candidate']:<24} rank={r['rank']:5.1f} "
                  f"gate={'PASS' if r['gate'] else 'FAIL'} → {r['lifecycle']}")
    return 0


def _hc_ping(slug: str, fail: bool = False) -> None:
    """Healthchecks.io ping. 키가 없으면 조용히 생략 (감시자 부재가 실행을 막지 않는다)."""
    key = os.environ.get("HEALTHCHECKS_PING_KEY")
    if not key:
        print("[heartbeat] HEALTHCHECKS_PING_KEY 미설정 — ping 생략 (SETUP_REQUIRED)")
        return
    url = f"https://hc-ping.com/{key}/{slug}" + ("/fail" if fail else "")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(f"[heartbeat] healthchecks ping {slug}{'/fail' if fail else ''}: {r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] healthchecks ping 실패 (무시하고 진행): {e}", file=sys.stderr)


def cmd_heartbeat() -> int:
    """infra-heartbeat (M1.5 §7): GitHub Action → DB 기록 → health 갱신 → HC ping.

    production collector가 아니다 — 인프라 신뢰 사슬 검증 전용.
    """
    now = utcnow()
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    trigger = "manual" if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" \
        else ("schedule" if os.environ.get("GITHUB_EVENT_NAME") == "schedule" else "local")
    try:
        with db.connect() as conn:
            wr = db.one(
                conn,
                """
                insert into workflow_runs
                  (workflow_name, github_run_id, github_sha, trigger_type,
                   started_at, status)
                values ('infra-heartbeat', %s, %s, %s, %s, 'running') returning id
                """,
                (int(github_run_id) if github_run_id else None,
                 os.environ.get("GITHUB_SHA"), trigger, now),
            )
            check_id = connectivity_check.check(conn)
            statuses = freshness.update_system_health(conn, now)
            db.execute(
                conn,
                """
                update workflow_runs set status = 'success', completed_at = %s,
                  duration_seconds = extract(epoch from (%s - started_at))
                where id = %s
                """,
                (utcnow(), utcnow(), wr["id"]),
            )
        print(f"[heartbeat] workflow_runs 기록 OK (run={wr['id']}, trigger={trigger})")
        print(f"[heartbeat] connectivity write/read/delete OK (id={check_id})")
        print(f"[heartbeat] system_health 갱신: {len(statuses)}개 소스")
        _hc_ping("infra-heartbeat")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] FAILED: {e}", file=sys.stderr)
        _hc_ping("infra-heartbeat", fail=True)
        return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="workers.run")
    p.add_argument("command", choices=["mock-pipeline", "health", "score", "heartbeat"])
    args = p.parse_args()
    return {"mock-pipeline": cmd_mock_pipeline,
            "health": cmd_health,
            "score": cmd_score,
            "heartbeat": cmd_heartbeat}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
