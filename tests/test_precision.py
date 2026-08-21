"""published_precision 보호 규칙 (docs/DATABASE.md §2).

핵심: DAY/UNKNOWN 데이터는 6h velocity/acceleration의 primary evidence가 될 수 없다.
"""
from datetime import timedelta

from workers.discovery.candidates import effective_at
from workers.lib.timeutil import KST, utcnow
from workers.scoring.pipeline import _count_all, _count_precise

NOW = utcnow()


def mention(hash_, precision, hours_ago):
    return {"content_hash": hash_, "published_precision": precision,
            "effective_at": NOW - timedelta(hours=hours_ago)}


class TestSixHourWindowExcludesImprecise:
    def test_day_precision_excluded_from_6h(self):
        mentions = [
            mention("a", "SECOND", 1),
            mention("b", "MINUTE", 2),
            mention("c", "DAY", 1),       # 6h 창 안이어도 제외되어야 함
            mention("d", "UNKNOWN", 1),   # 제외
        ]
        assert _count_precise(mentions, NOW - timedelta(hours=6), NOW) == 2

    def test_day_only_term_has_zero_6h_count(self):
        # Blog(DAY)/Cafe(UNKNOWN)만 있는 키워드 → 6h velocity의 근거 0
        mentions = [mention("a", "DAY", 1), mention("b", "UNKNOWN", 2)]
        assert _count_precise(mentions, NOW - timedelta(hours=6), NOW) == 0

    def test_day_included_in_24h_window(self):
        # 24h+ 윈도우·supply·cross-source에는 포함된다
        mentions = [mention("a", "DAY", 1), mention("b", "UNKNOWN", 2),
                    mention("c", "SECOND", 3)]
        assert _count_all(mentions, NOW - timedelta(hours=24), NOW) == 3

    def test_syndicated_duplicates_count_once(self):
        # 같은 content_hash → 1개 문서 (명세 §61 Test D)
        mentions = [mention("same", "SECOND", 1) for _ in range(10)]
        assert _count_precise(mentions, NOW - timedelta(hours=6), NOW) == 1
        assert _count_all(mentions, NOW - timedelta(hours=6), NOW) == 1


class TestEffectiveAt:
    def test_second_uses_published(self):
        pub = NOW - timedelta(hours=3)
        assert effective_at(pub, "SECOND", NOW) == pub

    def test_day_uses_kst_midnight(self):
        pub = NOW - timedelta(hours=3)
        eff = effective_at(pub, "DAY", NOW)
        k = eff.astimezone(KST)
        assert (k.hour, k.minute, k.second) == (0, 0, 0)

    def test_unknown_uses_first_seen(self):
        seen = NOW - timedelta(hours=5)
        assert effective_at(None, "UNKNOWN", seen) == seen
