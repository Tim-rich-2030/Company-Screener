"""Freshness 장애 시뮬레이션 CLI (명세 §61 Test A/C의 수동 재현 도구).

사용:
  # 수집 실패 시뮬레이션: 마지막 성공을 SLA 밖으로 밀어내고 실패 run을 기록
  python -m workers.tools.simulate_failure --source naver_search

  # stale data 시뮬레이션: 수집은 성공 상태 그대로, source_data_through만 과거로
  python -m workers.tools.simulate_failure --source naver_search_trend --stale-days 3

실행 후 자동으로 system_health 갱신 + scoring 재실행:
  → 해당 소스 RED, Freshness Gate FAIL, TODAY의 NOW(VERIFIED) 추천 제거.
원복은 mock 모드에서 `RADAR_MODE=mock python -m workers.run mock-pipeline` 재실행.
"""
from __future__ import annotations

import argparse

from ..lib import config, db
from ..lib.timeutil import utcnow
from ..scoring import freshness, pipeline


def simulate(source_or_alias: str, hours: float, stale_days: int | None,
             rescore: bool = True) -> None:
    names = config.resolve_source_names(source_or_alias)
    now = utcnow()
    with db.connect() as conn:
        for name in names:
            src = db.one(conn, "select source_id from sources where name = %s", (name,))
            if src is None:
                raise SystemExit(f"unknown source: {name}")

            if stale_days is not None:
                # Test C 재현: fetch는 성공(최근)인데 데이터 자체가 오래됨
                db.execute(
                    conn,
                    """
                    update source_runs
                    set source_data_through = source_data_through - interval '1 day' * %s
                    where source_id = %s and status = 'success'
                    """,
                    (stale_days, src["source_id"]),
                )
                print(f"[stale] {name}: source_data_through -{stale_days}d "
                      f"(마지막 성공 시각은 그대로)")
            else:
                # Test A 재현: 마지막 성공을 SLA 밖으로 이동 + 실패 run 기록
                db.execute(
                    conn,
                    """
                    update source_runs
                    set started_at = started_at - interval '1 hour' * %s,
                        completed_at = completed_at - interval '1 hour' * %s,
                        source_data_through = source_data_through - interval '1 hour' * %s
                    where source_id = %s
                    """,
                    (hours, hours, hours, src["source_id"]),
                )
                wr = db.one(
                    conn,
                    """
                    insert into workflow_runs (workflow_name, trigger_type, started_at,
                                               completed_at, status, error_message)
                    values ('simulate-failure', 'test', %s, %s, 'failed',
                            'SIMULATED FAILURE') returning id
                    """,
                    (now, now),
                )
                db.execute(
                    conn,
                    """
                    insert into source_runs (source_id, workflow_run_id, started_at,
                                             completed_at, status, http_status, error)
                    values (%s, %s, %s, %s, 'failed', 500,
                            'SIMULATED FAILURE (simulate_failure CLI)')
                    """,
                    (src["source_id"], wr["id"], now, now),
                )
                print(f"[fail] {name}: 마지막 성공 -{hours}h 이동 + 실패 run 기록")

        if rescore:
            results = pipeline.run_scoring(conn, now, demand=None, trigger="test")
            for r in sorted(results, key=lambda x: -x["rank"]):
                print(f"[rescore] {r['candidate']:<24} "
                      f"gate={'PASS' if r['gate'] else 'FAIL'} → {r['lifecycle']}")
        else:
            freshness.update_system_health(conn, now)
        health = db.all_rows(
            conn, "select component, status from system_health order by component")
        print("[health] " + ", ".join(f"{h['component']}={h['status']}" for h in health))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True,
                   help="소스명 또는 별칭 (예: naver_search → news/blog/cafe)")
    p.add_argument("--hours", type=float, default=4.0,
                   help="마지막 성공을 과거로 미는 시간 (기본 4h — 모든 realtime SLA 밖)")
    p.add_argument("--stale-days", type=int, default=None,
                   help="지정 시 stale-data 모드: 성공 상태 유지, data_through만 N일 과거로")
    p.add_argument("--no-rescore", action="store_true")
    a = p.parse_args()
    simulate(a.source, a.hours, a.stale_days, rescore=not a.no_rescore)


if __name__ == "__main__":
    main()
