"""시간 처리 단일 정의처.

규칙 (docs/DATA_FRESHNESS.md §1):
- 저장·계산은 전부 UTC aware datetime. naive datetime 금지.
- 표시만 KST(UTC+9, DST 없음).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
KST = timezone(timedelta(hours=9), name="KST")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_kst(dt: datetime) -> datetime:
    _require_aware(dt)
    return dt.astimezone(KST)


def kst_midnight(dt: datetime) -> datetime:
    """dt가 속한 KST 날짜의 자정을 UTC aware로 반환 (DAY precision 저장 규칙)."""
    _require_aware(dt)
    k = dt.astimezone(KST)
    return k.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def minutes_between(later: datetime, earlier: datetime) -> float:
    _require_aware(later)
    _require_aware(earlier)
    return (later - earlier).total_seconds() / 60.0


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None:
        raise ValueError("naive datetime 금지 — 모든 datetime은 tz-aware(UTC)여야 한다")
