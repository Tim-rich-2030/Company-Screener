"""scoring 공식 unit test (명세 §26~§36)."""
import json
from pathlib import Path

import pytest

from workers.scoring import formulas as F

CFG = json.loads((Path(__file__).resolve().parents[1] / "config" / "scoring.v1.json")
                 .read_text(encoding="utf-8"))


class TestVelocity:
    def test_formula(self):
        # (current+1)/(baseline+1) — 명세 §26
        assert F.velocity(9, 1.0, 10, CFG) == pytest.approx(5.0)

    def test_zero_baseline_no_division_error(self):
        assert F.velocity(4, 0.0, 10, CFG) == pytest.approx(5.0)

    def test_sparse_cap(self):
        # distinct_documents < 4 → 폭주 제한 (명세 §26)
        sparse = F.velocity(9, 0.0, 3, CFG)
        assert sparse == CFG["velocity"]["sparse_velocity_cap"]

    def test_hard_cap(self):
        assert F.velocity(1000, 0.0, 100, CFG) == CFG["velocity"]["velocity_cap"]


class TestAcceleration:
    def test_formula(self):
        # v_cur=(8+1)/(2+1)=3, v_prev=(2+1)/(0+1)=3 → 1.0 (명세 §27)
        assert F.acceleration(8, 2, 0, CFG) == pytest.approx(1.0)

    def test_accelerating(self):
        # v_cur=(6+1)/(2+1)=2.33, v_prev=(2+1)/(1+1)=1.5 → 1.55
        assert F.acceleration(6, 2, 1, CFG) == pytest.approx(2.333 / 1.5, rel=1e-3)

    def test_cap(self):
        assert F.acceleration(1000, 1, 100, CFG) == CFG["velocity"]["acceleration_cap"]


class TestNovelty:
    def test_new_term_full(self):
        assert F.novelty(0, CFG) == 100.0
        assert F.novelty(CFG["novelty"]["max_mentions_30d_for_full"], CFG) == 100.0

    def test_established_zero(self):
        assert F.novelty(CFG["novelty"]["zero_novelty_mentions_30d"], CFG) == 0.0

    def test_monotonic_decrease(self):
        vals = [F.novelty(m, CFG) for m in (3, 10, 50, 100, 200)]
        assert vals == sorted(vals, reverse=True)


class TestCrossSource:
    def test_table(self):
        # 명세 §29 매핑표 그대로
        assert F.cross_source_score(1, CFG) == 20
        assert F.cross_source_score(2, CFG) == 50
        assert F.cross_source_score(3, CFG) == 75
        assert F.cross_source_score(4, CFG) == 90
        assert F.cross_source_score(5, CFG) == 100
        assert F.cross_source_score(9, CFG) == 100
        assert F.cross_source_score(0, CFG) == 0


class TestWeighted:
    def test_weights_must_sum_100(self):
        with pytest.raises(ValueError):
            F.weighted({"a": 50.0}, {"a": 99})

    def test_all_weight_tables_sum_100(self):
        for key in ("early_signal_weights", "opportunity_weights", "confidence_weights"):
            assert round(sum(CFG[key].values())) == 100, key


class TestRank:
    def test_formula_fixed(self):
        # rank = opp * (0.70 + 0.30*conf/100) — 명세 §35, AI 변경 금지
        assert F.rank_score(80, 100, CFG) == pytest.approx(80.0)
        assert F.rank_score(80, 0, CFG) == pytest.approx(56.0)
        assert F.rank_score(84, 88, CFG) == pytest.approx(84 * (0.7 + 0.3 * 0.88))


class TestLifecycle:
    def test_now_requires_gate_pass(self):
        # 명세 §36: NOW = opp>=75 AND conf>=70 AND gate PASS
        assert F.lifecycle_for(80, 80, True, CFG) == "now"
        assert F.lifecycle_for(80, 80, False, CFG) != "now"

    def test_boundaries(self):
        assert F.lifecycle_for(75, 70, True, CFG) == "now"
        assert F.lifecycle_for(74.9, 70, True, CFG) == "watch"
        assert F.lifecycle_for(75, 69.9, True, CFG) == "watch"
        assert F.lifecycle_for(55, 0, True, CFG) == "watch"
        assert F.lifecycle_for(54.9, 49.9, True, CFG) == "new"
