"""워커 단일 진입점.

사용:
  RADAR_MODE=mock python -m workers.run mock-pipeline   # fixture 기반 전체 파이프라인
  python -m workers.run health                          # system_health 갱신만
  python -m workers.run score                           # scoring만 재실행
"""
from __future__ import annotations

import argparse
import sys

from .collectors import mock as mock_collector
from .discovery import candidates as discovery
from .lib import config, db, registry
from .lib.timeutil import utcnow
from .scoring import freshness, pipeline


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


def main() -> int:
    p = argparse.ArgumentParser(prog="workers.run")
    p.add_argument("command", choices=["mock-pipeline", "health", "score"])
    args = p.parse_args()
    return {"mock-pipeline": cmd_mock_pipeline,
            "health": cmd_health,
            "score": cmd_score}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
