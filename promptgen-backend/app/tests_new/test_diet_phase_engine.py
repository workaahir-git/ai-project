"""Standalone sanity tests for diet_phase_engine.py — run directly:
    python3 -m app.test_diet_phase_engine
Not wired into any CI yet (no test runner exists in this app currently);
this is a first pass to catch obvious errors before Engine 39 gets called
from fitness_generator.py.
"""

from app.diet_phase_engine import compute_diet_phase

BASE_PROFILE = {
    "current_weight_kg": 78,
    "height_cm": 178,
    "age": 27,
    "gender": "male",
    "activity_level_factor": 1.55,
    "experience": "intermediate",
    "goal": "muscle gain",
}


def test_dp001_fat_loss_selects_cut():
    p = dict(BASE_PROFILE, goal="fat loss")
    r = compute_diet_phase(p)
    assert r["phase"] == "cut", r
    assert r["target_kcal"] < r["tdee_kcal"], r
    print("DP001 (fat loss -> cut): PASS", r["phase"], r["target_kcal"], "vs tdee", r["tdee_kcal"])


def test_dp002_novice_bulk():
    p = dict(BASE_PROFILE, goal="muscle gain", experience="beginner")
    r = compute_diet_phase(p)
    assert r["phase"] == "bulk", r
    assert r["target_kcal"] > r["tdee_kcal"], r
    print("DP002 (novice hypertrophy -> bulk): PASS", r["phase"])


def test_dp003_experienced_recomp():
    p = dict(BASE_PROFILE, goal="muscle gain", experience="advanced")
    r = compute_diet_phase(p)
    assert r["phase"] == "recomp", r
    print("DP003 (experienced hypertrophy -> recomp): PASS", r["phase"])


def test_dp004_caps_deficit_when_recovery_low():
    p = dict(BASE_PROFILE, goal="fat loss")
    r_normal = compute_diet_phase(p, recovery_capacity_score=80)
    r_capped = compute_diet_phase(p, recovery_capacity_score=30)
    assert r_capped["flags"]["deficit_capped_by_recovery"] is True
    assert r_normal["flags"]["deficit_capped_by_recovery"] is False
    # capped deficit should be SMALLER (less negative) than uncapped
    assert (r_capped["tdee_kcal"] - r_capped["target_kcal"]) < (r_normal["tdee_kcal"] - r_normal["target_kcal"])
    print("DP004 (low recovery caps deficit at 15%): PASS",
          "uncapped deficit kcal:", r_normal["tdee_kcal"] - r_normal["target_kcal"],
          "capped deficit kcal:", r_capped["tdee_kcal"] - r_capped["target_kcal"])


def test_dp005_ed_disclosure_blocks_deficit():
    p = dict(BASE_PROFILE, goal="fat loss")
    r = compute_diet_phase(p, notes_raw="I have a history of anorexia and want to be careful")
    assert r["phase"] == "maintenance", r
    assert r["flags"]["ed_history_disclosed"] is True
    assert r["target_kcal"] == r["tdee_kcal"], r
    print("DP005 (disclosed ED history -> maintenance, no deficit): PASS")


def test_dp005_does_not_false_fire_on_casual_diet_talk():
    p = dict(BASE_PROFILE, goal="fat loss")
    r = compute_diet_phase(p, notes_raw="trying to eat less junk food and skip snacking at night")
    assert r["flags"]["ed_history_disclosed"] is False
    assert r["phase"] == "cut", r
    print("DP005 (casual diet language does NOT false-trigger): PASS")


def test_dp006_needs_reassessment_after_duration():
    p = dict(BASE_PROFILE, goal="fat loss")  # cut, ~9wk duration
    r_early = compute_diet_phase(p, phase_start_cycle=1, current_cycle=3)
    r_late = compute_diet_phase(p, phase_start_cycle=1, current_cycle=12)
    assert r_early["flags"]["needs_reassessment"] is False
    assert r_late["flags"]["needs_reassessment"] is True
    print("DP006 (reassessment flag after phase duration elapses): PASS")


def test_dp006_no_false_flag_without_cycle_data():
    p = dict(BASE_PROFILE, goal="fat loss")
    r = compute_diet_phase(p)  # no phase_start_cycle/current_cycle supplied
    assert r["flags"]["needs_reassessment"] is False
    print("DP006 (no cycle data -> no guessed reassessment flag): PASS")


def test_macro_split_sums_reasonably_to_target_kcal():
    p = dict(BASE_PROFILE, goal="muscle gain", experience="advanced")
    r = compute_diet_phase(p)
    m = r["macro_split"]
    recomposed_kcal = m["protein_g"] * 4 + m["carbs_g"] * 4 + m["fat_g"] * 9
    assert abs(recomposed_kcal - r["target_kcal"]) <= 5, (recomposed_kcal, r["target_kcal"])
    print("Macro split reconstructs target_kcal within rounding: PASS")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)}/{len(tests)} diet_phase_engine tests passed.")
