"""Clock Integrity (M1.5 §13): timezone-aware datetime만 허용.

정적 검사 — workers/ 전체에서 naive datetime을 만드는 호출을 금지한다.
런타임 방어는 workers/lib/timeutil._require_aware가 담당한다.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKERS = Path(__file__).resolve().parents[1] / "workers"

FORBIDDEN = [
    # datetime.utcnow()는 naive를 반환 — 전면 금지 (timeutil.utcnow()를 쓴다)
    (re.compile(r"datetime\.utcnow\s*\("), "datetime.utcnow() 금지 — timeutil.utcnow() 사용"),
    # 인자 없는 datetime.now() — tz 없는 naive
    (re.compile(r"datetime\.now\s*\(\s*\)"), "tz 없는 datetime.now() 금지"),
    # naive today 기반 date 계산
    (re.compile(r"date\.today\s*\("), "date.today() 금지 — KST 명시 변환 사용"),
    # tz 없는 fromtimestamp
    (re.compile(r"fromtimestamp\s*\(\s*[^,)]+\s*\)"), "tz 없는 fromtimestamp 금지"),
]


def _worker_sources():
    return sorted(WORKERS.rglob("*.py"))


def test_workers_directory_exists():
    assert WORKERS.is_dir() and _worker_sources()


def test_no_naive_datetime_construction():
    violations = []
    for f in _worker_sources():
        text = f.read_text(encoding="utf-8")
        for pattern, msg in FORBIDDEN:
            for m in pattern.finditer(text):
                line = text[: m.start()].count("\n") + 1
                violations.append(f"{f.relative_to(WORKERS.parent)}:{line} — {msg}")
    assert not violations, "naive datetime 사용 발견:\n" + "\n".join(violations)


def test_timeutil_rejects_naive():
    from datetime import datetime

    import pytest

    from workers.lib.timeutil import minutes_between, to_kst, utcnow

    naive = datetime(2026, 8, 21, 12, 0, 0)
    with pytest.raises(ValueError):
        to_kst(naive)
    with pytest.raises(ValueError):
        minutes_between(utcnow(), naive)


def test_utcnow_is_aware_utc():
    from workers.lib.timeutil import UTC, utcnow

    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0
    assert now.tzinfo == UTC
