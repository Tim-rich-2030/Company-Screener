"""DB integration + failure injection 테스트 (명세 §60 Integration, §61 Test A/C/D).

로컬 PostgreSQL 필요 (TEST_PG_ADMIN_URL). migration → mock 수집 → discovery →
scoring → health 전체를 실제 DB에서 검증한다.
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from workers.collectors import mock as mock_collector
from workers.discovery import candidates as discovery
from workers.lib import config as cfg_mod
from workers.lib import registry
from workers.lib.timeutil import utcnow
from workers.scoring import pipeline
from workers.tools.simulate_failure import simulate


@pytest.fixture()
def pipeline_db(test_db):
    """mock 파이프라인 1회 실행이 끝난 DB."""
    now = utcnow()
    with psycopg.connect(test_db, row_factory=dict_row) as conn:
        registry.sync_sources(conn)
        mock_collector.run_mock_collection(conn, now)
        discovery.rebuild_terms_and_mentions(conn, now)
        discovery.create_candidates(conn, now, cfg_mod.scoring_config())
        pipeline.run_scoring(conn, now, demand=mock_collector.load_fixture_demand(),
                             trigger="test")
        conn.commit()
    return test_db


def q(url, sql, params=None):
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


class TestMockPipeline:
    def test_creates_at_least_3_candidates(self, pipeline_db):
        rows = q(pipeline_db, "select count(*) as n from candidates")
        assert rows[0]["n"] >= 3

    def test_today_has_now_candidate_with_pass(self, pipeline_db):
        rows = q(pipeline_db,
                 "select * from v_today where lifecycle = 'now' and freshness_pass")
        assert len(rows) >= 1

    def test_all_sources_green_after_collection(self, pipeline_db):
        rows = q(pipeline_db, """
            select h.status from system_health h
            join sources s on s.name = h.component where s.enabled""")
        assert rows and all(r["status"] == "GREEN" for r in rows)

    def test_data_cutoff_populated(self, pipeline_db):
        row = q(pipeline_db, "select * from v_data_cutoff")[0]
        assert row["market_complete_through"] is not None
        assert row["policy_complete_through"] is not None
        assert row["last_pipeline_at"] is not None
        assert row["overall_status"] == "GREEN"

    def test_syndicated_duplicates_not_counted_twice(self, pipeline_db):
        # fixture의 c1dupA/c1dupB(동일 기사 사본)는 문서 1건으로 집계 (Test D)
        rows = q(pipeline_db, """
            select cm.distinct_documents from candidate_metrics cm
            join candidates c on c.id = cm.candidate_id
            where c.cluster_name = '오즈모포켓4 축구촬영'
            order by cm.window_end desc limit 1""")
        # 6h 창의 시간정밀 문서: news 5 + 신디케이션 1(2건→1) + youtube 2 + trends 1 = 9
        assert rows[0]["distinct_documents"] == 9

    def test_snapshot_append_only(self, pipeline_db):
        with pytest.raises(psycopg.errors.RaiseException):
            q(pipeline_db, "update score_snapshots set opportunity = 0 returning id")
        with pytest.raises(psycopg.errors.RaiseException):
            q(pipeline_db, "delete from score_snapshots returning id")

    def test_rescoring_appends_not_overwrites(self, pipeline_db):
        n0 = q(pipeline_db, "select count(*) as n from score_snapshots")[0]["n"]
        with psycopg.connect(pipeline_db, row_factory=dict_row) as conn:
            pipeline.run_scoring(conn, utcnow(), trigger="test")
            conn.commit()
        n1 = q(pipeline_db, "select count(*) as n from score_snapshots")[0]["n"]
        assert n1 > n0  # 기존 snapshot 덮어쓰기 금지 (명세 §55)

    def test_rerun_collection_is_idempotent(self, pipeline_db):
        n0 = q(pipeline_db, "select count(*) as n from source_items")[0]["n"]
        with psycopg.connect(pipeline_db, row_factory=dict_row) as conn:
            stats = mock_collector.run_mock_collection(conn, utcnow())
            conn.commit()
        n1 = q(pipeline_db, "select count(*) as n from source_items")[0]["n"]
        assert n1 == n0
        assert stats["items_new"] == 0


class TestFailureInjection:
    """명세 §61 Test A: Naver 실패 → RED → gate FAIL → NOW 추천 제거."""

    def test_naver_failure_removes_verified_recommendation(self, pipeline_db, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", pipeline_db)
        assert q(pipeline_db, "select 1 from v_today where lifecycle = 'now'")

        simulate("naver_search", hours=4.0, stale_days=None, rescore=True)

        reds = q(pipeline_db, """
            select component from system_health
            where status = 'RED' order by component""")
        assert {r["component"] for r in reds} >= {"naver_news", "naver_blog", "naver_cafe"}

        cutoff = q(pipeline_db, "select overall_status from v_data_cutoff")[0]
        assert cutoff["overall_status"] == "RED"

        latest = q(pipeline_db, "select freshness_pass, lifecycle from v_today")
        assert all(not r["freshness_pass"] for r in latest)
        assert all(r["lifecycle"] != "now" for r in latest)

        alerts = q(pipeline_db,
                   "select component from alerts where resolved_at is null")
        assert len(alerts) >= 3


class TestStaleDataDetection:
    """명세 §61 Test C: fetch 성공이어도 source_data_through가 오래되면 RED."""

    def test_stale_daily_source_goes_red(self, pipeline_db, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", pipeline_db)
        before = q(pipeline_db, """
            select status from system_health where component = 'naver_search_trend'""")
        assert before[0]["status"] == "GREEN"

        simulate("naver_search_trend", hours=4.0, stale_days=3, rescore=False)

        row = q(pipeline_db, """
            select h.status, h.last_success_at from system_health h
            where component = 'naver_search_trend'""")[0]
        assert row["status"] == "RED"
        # 마지막 성공 시각은 최신 그대로 — fetch 성공만으로 GREEN 처리하지 않음을 증명
        assert (utcnow() - row["last_success_at"]).total_seconds() < 3600


class TestPrecisionInPipeline:
    def test_day_and_unknown_items_stored_with_precision(self, pipeline_db):
        rows = q(pipeline_db, """
            select s.name, si.published_precision,
                   count(*) filter (where si.published_at is null) as null_published
            from source_items si join sources s on s.source_id = si.source_id
            where s.name in ('naver_blog', 'naver_cafe')
            group by 1, 2""")
        by = {r["name"]: r for r in rows}
        assert by["naver_blog"]["published_precision"] == "DAY"
        assert by["naver_cafe"]["published_precision"] == "UNKNOWN"
        assert by["naver_cafe"]["null_published"] > 0

    def test_blog_only_supply_does_not_create_6h_velocity(self, pipeline_db):
        # DAY/UNKNOWN mention은 6h distinct_documents(=velocity 표본)에 기여하지 않는다
        rows = q(pipeline_db, """
            select cm.distinct_documents, cm.content_supply from candidate_metrics cm
            join candidates c on c.id = cm.candidate_id
            where c.cluster_name = '로봇청소기 물걸레'
            order by cm.window_end desc limit 1""")
        m = rows[0]
        # blog 5건·cafe 3건이 있어도 6h 표본은 시간정밀(news 4 + youtube 1)뿐
        assert m["distinct_documents"] == 5
        assert m["content_supply"]["blog_7d"] >= 3
