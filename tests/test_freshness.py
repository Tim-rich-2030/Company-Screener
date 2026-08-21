"""Freshness 판정 unit test (docs/DATA_FRESHNESS.md §3, §6 — 명세 §20~§21, §61 Test C)."""
from datetime import timedelta

from workers.lib.timeutil import KST, utcnow
from workers.scoring.freshness import GREEN, RED, YELLOW, freshness_gate, source_status

RT_SLA = {"green_lt": 90, "red_gte": 180}
DAILY_SLA = {"expected_lag_days": 1}
NOW = utcnow()


def ago(minutes=0, days=0):
    return NOW - timedelta(minutes=minutes, days=days)


class TestRealtimeStatus:
    def test_never_succeeded_is_red(self):
        assert source_status("realtime", RT_SLA, None, None, NOW) == RED

    def test_boundaries(self):
        assert source_status("realtime", RT_SLA, ago(minutes=89), NOW, NOW) == GREEN
        assert source_status("realtime", RT_SLA, ago(minutes=90), NOW, NOW) == YELLOW
        assert source_status("realtime", RT_SLA, ago(minutes=179), NOW, NOW) == YELLOW
        assert source_status("realtime", RT_SLA, ago(minutes=180), NOW, NOW) == RED
        assert source_status("realtime", RT_SLA, ago(minutes=181), NOW, NOW) == RED


class TestDailyStatus:
    """명세 §61 Test C: fetch 성공만으로 GREEN이 되면 안 된다."""

    def _midnight_kst_days_ago(self, d):
        k = NOW.astimezone(KST)
        return (k.replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=d))

    def test_fresh_fetch_with_d1_data_is_green(self):
        assert source_status("daily", DAILY_SLA, ago(minutes=10),
                             self._midnight_kst_days_ago(1), NOW) == GREEN

    def test_fresh_fetch_with_stale_data_is_not_green(self):
        # 핵심: 마지막 성공은 10분 전(최신)이지만 데이터가 D-3 → RED
        assert source_status("daily", DAILY_SLA, ago(minutes=10),
                             self._midnight_kst_days_ago(3), NOW) == RED

    def test_d2_is_yellow(self):
        assert source_status("daily", DAILY_SLA, ago(minutes=10),
                             self._midnight_kst_days_ago(2), NOW) == YELLOW

    def test_missed_daily_run_is_red(self):
        assert source_status("daily", DAILY_SLA, ago(minutes=27 * 60),
                             self._midnight_kst_days_ago(1), NOW) == RED

    def test_no_data_through_is_red(self):
        assert source_status("daily", DAILY_SLA, ago(minutes=10), None, NOW) == RED


ALL_GREEN = {
    "naver_news": GREEN, "naver_blog": GREEN, "naver_cafe": GREEN,
    "google_trends": GREEN, "youtube": GREEN,
    "policy_briefing": GREEN, "law_go_kr": GREEN, "lawmaking_notice": GREEN,
    "naver_search_trend": GREEN, "naver_searchad": GREEN,
}


class TestGate:
    def test_market_all_green_passes(self):
        assert freshness_gate("market", ALL_GREEN) is True

    def test_market_requires_all_naver(self):
        for s in ("naver_news", "naver_blog", "naver_cafe"):
            st = dict(ALL_GREEN, **{s: RED})
            assert freshness_gate("market", st) is False, s

    def test_market_yellow_is_not_pass(self):
        # YELLOW는 통과 불가 — GREEN만 인정 (docs/DATA_FRESHNESS.md §6)
        st = dict(ALL_GREEN, naver_news=YELLOW)
        assert freshness_gate("market", st) is False

    def test_market_trends_or_youtube(self):
        st = dict(ALL_GREEN, google_trends=RED)
        assert freshness_gate("market", st) is True     # youtube가 GREEN
        st = dict(ALL_GREEN, google_trends=RED, youtube=RED)
        assert freshness_gate("market", st) is False

    def test_policy_gate(self):
        ev = {"policy_briefing", "naver_news"}
        assert freshness_gate("policy", ALL_GREEN, ev) is True
        st = dict(ALL_GREEN, policy_briefing=RED)
        assert freshness_gate("policy", st, ev) is False
        # evidence에 정책 소스가 없으면 policy 후보 자격 없음
        assert freshness_gate("policy", ALL_GREEN, {"naver_news"}) is False

    def test_evergreen_gate(self):
        assert freshness_gate("evergreen", ALL_GREEN) is True
        st = dict(ALL_GREEN, naver_search_trend=RED)
        assert freshness_gate("evergreen", st) is False
