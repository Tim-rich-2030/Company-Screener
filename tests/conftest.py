"""pytest 공통 fixture.

DB integration 테스트는 로컬 PostgreSQL이 필요하다.
TEST_PG_ADMIN_URL(기본: postgres db)로 접속해 테스트 전용 DB를 만들고
supabase/migrations를 적용한 뒤, 끝나면 삭제한다.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
TEST_DB = "content_radar_test"


def _admin_url() -> str | None:
    return os.environ.get("TEST_PG_ADMIN_URL")


@pytest.fixture()
def test_db(monkeypatch):
    admin = _admin_url()
    if not admin:
        pytest.skip("TEST_PG_ADMIN_URL 미설정 — DB integration 테스트 생략")

    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'drop database if exists {TEST_DB}')
        c.execute(f'create database {TEST_DB}')

    url = admin.rsplit("/", 1)[0] + "/" + TEST_DB
    if "?" in admin:
        base, q = admin.split("?", 1)
        url = base.rsplit("/", 1)[0] + "/" + TEST_DB + "?" + q

    with psycopg.connect(url) as c:
        for m in MIGRATIONS:
            c.execute(m.read_text(encoding="utf-8"))
        c.commit()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("RADAR_MODE", "mock")
    yield url

    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'drop database if exists {TEST_DB} with (force)')
