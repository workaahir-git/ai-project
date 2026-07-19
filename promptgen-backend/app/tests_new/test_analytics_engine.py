from app.analytics_engine import compute_period_adherence, build_analytics_record


def test_adherence_pct_always_recomputed():
    r = compute_period_adherence(60, 54)
    assert r["adherence_pct"] == 90.0
    assert r["adherence_tier"] == "high"
    print("Adherence recomputed from sets_logged/sets_prescribed: PASS", r)


def test_spec_sample_high_tier():
    r = compute_period_adherence(60, 54)
    assert r["adherence_tier"] == "high"
    print("Spec sample AN_001 (90% -> high): PASS")


def test_spec_sample_low_tier():
    r = compute_period_adherence(48, 19)
    assert r["adherence_pct"] == 39.6
    assert r["adherence_tier"] == "low"
    print("Spec sample AN_002 (39.6% -> low): PASS")


def test_tier_boundaries_inclusive():
    assert compute_period_adherence(100, 80)["adherence_tier"] == "high"    # exactly 80%
    assert compute_period_adherence(100, 50)["adherence_tier"] == "moderate"  # exactly 50%
    assert compute_period_adherence(100, 49)["adherence_tier"] == "low"
    print("Tier boundaries inclusive at lower bound: PASS")


def test_an002_progression_eligibility():
    periods = [{"sets_prescribed": 50, "sets_logged": 45}] * 4  # 4 consecutive high weeks
    r = build_analytics_record(periods)
    assert r["consecutive_high_weeks"] == 4
    assert r["eligible_for_progression"] is True
    print("AN002 (4+ consecutive high weeks -> progression eligible): PASS")


def test_an002_not_eligible_with_gap():
    periods = [
        {"sets_prescribed": 50, "sets_logged": 45},
        {"sets_prescribed": 50, "sets_logged": 20},  # low week breaks streak
        {"sets_prescribed": 50, "sets_logged": 45},
        {"sets_prescribed": 50, "sets_logged": 45},
    ]
    r = build_analytics_record(periods)
    assert r["consecutive_high_weeks"] == 2
    assert r["eligible_for_progression"] is False
    print("Streak resets on a non-high week: PASS")


def test_an001_routes_to_feedback_after_2_low_periods():
    periods = [{"sets_prescribed": 50, "sets_logged": 10}] * 2
    r = build_analytics_record(periods)
    engines = [x["engine"] for x in r["routing"]]
    assert "feedback_engine" in engines
    print("AN001 (2+ consecutive low -> routes to feedback_engine): PASS")


def test_an001_does_not_fire_on_single_low_period():
    periods = [
        {"sets_prescribed": 50, "sets_logged": 45},
        {"sets_prescribed": 50, "sets_logged": 10},
    ]
    r = build_analytics_record(periods)
    engines = [x["engine"] for x in r["routing"]]
    assert "feedback_engine" not in engines
    print("AN001 does not false-fire on a single low period: PASS")


def test_an003_routes_to_recovery_capacity():
    periods = [
        {"sets_prescribed": 50, "sets_logged": 48},
        {"sets_prescribed": 50, "sets_logged": 47},
        {"sets_prescribed": 50, "sets_logged": 41},  # 82% -- still "high" tier, but volume trending down
    ]
    r = build_analytics_record(periods)
    engines = [x["engine"] for x in r["routing"]]
    assert "recovery_capacity_engine" in engines
    print("AN003 (volume down + high adherence -> recovery_capacity_engine): PASS")


def test_an004_routes_to_plateau_after_3_declines():
    periods = [
        {"sets_prescribed": 50, "sets_logged": 48, "top_working_weight_kg": 100},
        {"sets_prescribed": 50, "sets_logged": 48, "top_working_weight_kg": 97},
        {"sets_prescribed": 50, "sets_logged": 48, "top_working_weight_kg": 95},
        {"sets_prescribed": 50, "sets_logged": 48, "top_working_weight_kg": 92},
    ]
    r = build_analytics_record(periods)
    assert r["strength_trend"] == "decreasing"
    engines = [x["engine"] for x in r["routing"]]
    assert "plateau_engine" in engines
    print("AN004 (strength down 3+ periods, high adherence -> plateau_engine): PASS")


def test_no_trend_fabricated_with_single_period():
    periods = [{"sets_prescribed": 50, "sets_logged": 45, "top_working_weight_kg": 100}]
    r = build_analytics_record(periods)
    assert r["volume_trend"] == "insufficient_data"
    assert r["strength_trend"] == "insufficient_data"
    print("Single period -> insufficient_data, no fabricated trend: PASS")


def test_empty_periods_no_crash():
    r = build_analytics_record([])
    assert r["adherence_pct"] is None
    assert r["routing"] == []
    print("Empty periods list handled without crashing or fabricating: PASS")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)}/{len(tests)} analytics_engine tests passed.")
