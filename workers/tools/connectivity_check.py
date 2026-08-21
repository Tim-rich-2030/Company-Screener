"""GitHub Actions runner → Supabase 연결성 검사 (Milestone 1.5 §8).

전용 테이블(infra_connectivity_checks)에 테스트 row를 write → read → delete.
운영 데이터 테이블은 건드리지 않는다. 실패 시 exit code 1.
"""
from __future__ import annotations

import os
import sys

from ..lib import db
from ..lib.timeutil import utcnow


def check(conn) -> str:
    run_id = os.environ.get("GITHUB_RUN_ID")
    note = f"connectivity check {utcnow().isoformat()}"
    row = db.one(
        conn,
        """
        insert into infra_connectivity_checks (github_run_id, note)
        values (%s, %s) returning id
        """,
        (int(run_id) if run_id else None, note),
    )
    read = db.one(
        conn, "select note from infra_connectivity_checks where id = %s", (row["id"],))
    if read is None or read["note"] != note:
        raise RuntimeError("read-back mismatch")
    deleted = db.execute(
        conn, "delete from infra_connectivity_checks where id = %s", (row["id"],))
    if deleted != 1:
        raise RuntimeError("delete failed")
    return str(row["id"])


def main() -> int:
    try:
        with db.connect() as conn:
            check_id = check(conn)
        print(f"[connectivity] write/read/delete OK (id={check_id})")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[connectivity] FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
