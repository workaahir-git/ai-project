"""Standalone sanity tests for warmup_ramp_engine.py — run directly:
    python3 -m app.test_warmup_ramp_engine
"""

from app.warmup_ramp_engine import build_warmup_ramp


def test_spec_worked_example_barbell_squat():
    # Directly from KB engines["40"].spec_text's sample profile RAMP_001.
    exercise = {"exercise_id": "barbell_back_squat", "requires": "Barbell", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=112, working_set_pct=80)
    weights = [s["weight_kg"] for s in r["ramp_sets"]]
    # Spec sample: 45, 61.5, 78.5, 95, 106.5, 112 — our rounding granularity
    # (nearest 1.25kg-multiple per side) should land within a couple kg.
    expected_approx = [45, 61.5, 78.5, 95, 106.5, 112]
    for got, exp in zip(weights, expected_approx):
        assert abs(got - exp) <= 2.5, (weights, expected_approx)
    assert r["ramp_sets"][-1]["reps"] == "as prescribed"
    print("Spec worked example (barbell squat, 112kg): PASS", weights)


def test_spec_worked_example_unloadable_isolation():
    # Spec sample RAMP_002 — leg extension, no working weight logged yet
    # (the actual unloadable condition is "no working_weight_kg", not the
    # requires=None tag by itself -- see machine/cable bugfix test below).
    exercise = {"exercise_id": "leg_extension", "requires": None, "slot": "isolation"}
    r = build_warmup_ramp(exercise, working_weight_kg=None)
    assert all(s["weight_kg"] is None for s in r["ramp_sets"])
    assert len(r["ramp_sets"]) == 2
    print("Spec worked example (no logged baseline -> RPE-anchored): PASS")


def test_machine_cable_with_requires_none_gets_numeric_ramp():
    # BUGFIX regression: exercise_database.py tags machine/cable exercises
    # (e.g. "Machine Chest Press", "Single-Arm Cable Row") as
    # requires=None -- same as bodyweight -- but they DO have a real
    # logged working weight. These must NOT fall back to RPE-anchored just
    # because the tag is None; only bands/TRX (explicit tag) or a missing
    # working_weight_kg should trigger RPE-only.
    exercise = {"exercise_id": "machine_chest_press", "requires": None, "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=62.5)
    assert r["working_weight_kg"] is not None, r
    assert all(s["weight_kg"] is not None for s in r["ramp_sets"]), r["ramp_sets"]
    print("Machine/cable (requires=None) with real working weight -> numeric ramp: PASS", [s["weight_kg"] for s in r["ramp_sets"]])


def test_isolation_always_gets_short_ramp():
    exercise = {"exercise_id": "cable_lateral_raise", "requires": "Dumbbells", "slot": "isolation"}
    r = build_warmup_ramp(exercise, working_weight_kg=10)
    # short ramp = 2 warm-up rows + 1 working row = 3 total
    assert len(r["ramp_sets"]) == 3, r["ramp_sets"]
    print("Isolation -> shortened ramp regardless of intensity: PASS")


def test_compound_gets_full_ramp():
    exercise = {"exercise_id": "barbell_back_squat", "requires": "Barbell", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=100)
    assert len(r["ramp_sets"]) == 6, r["ramp_sets"]  # 5 warm-up rows + 1 working row
    print("Compound -> full 6-row ramp: PASS")


def test_never_prescribes_below_bar_weight():
    exercise = {"exercise_id": "barbell_bench_press", "requires": "Barbell", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=30)  # light -> 40% would be 12kg, below bar
    assert all(s["weight_kg"] >= 20.0 for s in r["ramp_sets"]), r["ramp_sets"]
    print("Never rounds below bar weight (20kg floor): PASS")


def test_dumbbell_no_bar_weight_involved():
    exercise = {"exercise_id": "dumbbell_bench_press", "requires": "Dumbbells", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=30)
    assert r["bar_weight_kg"] is None
    assert r["smallest_plate_kg"] == 2.5
    for s in r["ramp_sets"]:
        assert s["weight_kg"] % 2.5 == 0 or s["weight_kg"] % 2.5 == 2.5, s
    print("Dumbbell ramp uses per-hand rounding, no bar weight: PASS")


def test_smith_machine_flags_assumed_bar_weight():
    exercise = {"exercise_id": "smith_squat", "requires": "Smith machine", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=80)
    assert r["bar_weight_is_assumed"] is True
    print("Smith machine flags its bar weight as an assumption: PASS")


def test_resistance_band_never_gets_numeric_weight():
    exercise = {"exercise_id": "band_pull_apart", "requires": "Resistance bands", "slot": "isolation"}
    r = build_warmup_ramp(exercise, working_weight_kg=None)
    assert all(s["weight_kg"] is None for s in r["ramp_sets"])
    print("Bands never get a fabricated numeric weight: PASS")


def test_no_working_weight_falls_back_rpe():
    exercise = {"exercise_id": "barbell_back_squat", "requires": "Barbell", "slot": "compound"}
    r = build_warmup_ramp(exercise, working_weight_kg=None)
    assert all(s["weight_kg"] is None for s in r["ramp_sets"])
    print("No logged baseline -> RPE fallback, no guessed 1RM/weight: PASS")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)}/{len(tests)} warmup_ramp_engine tests passed.")
