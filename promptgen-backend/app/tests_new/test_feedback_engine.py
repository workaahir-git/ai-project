from app.feedback_engine import classify_feedback, check_consecutive_pattern


def test_fb001_pain_overrides_rating():
    r = classify_feedback(3, "sharp twinge in my right shoulder on the last rep")
    assert r["classification"] == "possible_pain_flag"
    assert r["pain_keyword_detected"] is True
    assert r["routed_to"] == "safety_engine"
    print("FB001 (pain overrides mid rating): PASS")


def test_fb002_low_rating_too_easy():
    for rating in (1, 2):
        r = classify_feedback(rating, "felt light")
        assert r["classification"] == "too_easy", r
    print("FB002 (rating 1-2 -> too_easy): PASS")


def test_fb003_high_rating_too_hard():
    for rating in (4, 5):
        r = classify_feedback(rating, "really grinded that last set")
        assert r["classification"] == "too_hard", r
    print("FB003 (rating 4-5 -> too_hard): PASS")


def test_fb004_mid_rating_appropriate():
    r = classify_feedback(3, "felt good, could probably go heavier next time")
    assert r["classification"] == "appropriate"
    assert r["pain_keyword_detected"] is False
    print("FB004 (rating 3, no pain -> appropriate): PASS")


def test_fb005_no_rating_insufficient_data():
    r = classify_feedback(None, "did the workout")
    assert r["classification"] == "insufficient_data"
    print("FB005 (no rating -> insufficient_data): PASS")


def test_fb001_still_checked_when_no_rating():
    r = classify_feedback(None, "numbness down my arm afterward")
    assert r["classification"] == "possible_pain_flag"
    print("FB001+FB005 (pain check applies even with no rating): PASS")


def test_spec_sample_appropriate():
    r = classify_feedback(3, "felt good, could probably go heavier next time", exercise_id="barbell_back_squat")
    assert r == {
        "exercise_id": "barbell_back_squat",
        "difficulty_rating": 3,
        "classification": "appropriate",
        "pain_keyword_detected": False,
        "routed_to": None,
    }
    print("Spec sample FB_001 (appropriate): PASS")


def test_spec_sample_pain_flag():
    r = classify_feedback(3, "sharp pinch in my right shoulder on the last rep", exercise_id="overhead_press")
    assert r["classification"] == "possible_pain_flag"
    assert r["pain_keyword_detected"] is True
    print("Spec sample FB_002 (pain flag): PASS")


def test_spec_sample_too_easy_no_notes():
    r = classify_feedback(1, None, exercise_id="dumbbell_curl")
    assert r["classification"] == "too_easy"
    assert r["pain_keyword_detected"] is False
    print("Spec sample FB_003 (too_easy, no notes): PASS")


def test_consecutive_progression_suggestion():
    assert check_consecutive_pattern(["too_easy", "too_easy", "too_easy"]) == "suggest_progression"
    assert check_consecutive_pattern(["appropriate", "too_easy", "too_easy"]) is None
    print("Consecutive too_easy x3 -> suggest_progression: PASS")


def test_consecutive_regression_suggestion():
    assert check_consecutive_pattern(["too_hard", "too_hard", "too_hard"]) == "suggest_regression"
    print("Consecutive too_hard x3 -> suggest_regression: PASS")


def test_progression_engine_pain_keywords_still_covered():
    # Ensures the union didn't drop anything progression_engine.py already caught.
    r = classify_feedback(3, "there was a real tweak in my lower back")
    assert r["classification"] == "possible_pain_flag"
    print("Union includes progression_engine's original pain terms: PASS")


def test_negation_false_positive_fixed():
    # BUGFIX regression: "no pain" / "not sore" must NOT flag as pain.
    r = classify_feedback(3, "felt light, no pain at all, could've gone heavier")
    assert r["classification"] == "appropriate", r
    assert r["pain_keyword_detected"] is False, r
    print("Negation bugfix ('no pain' does not false-flag): PASS")


def test_negation_does_not_suppress_real_pain_reports():
    # Make sure the negation fix isn't overly broad -- a real pain report
    # elsewhere in the same sentence must still fire.
    r = classify_feedback(3, "not my best set, felt a sharp pinch in my shoulder")
    assert r["classification"] == "possible_pain_flag", r
    print("Negation fix does not suppress a real, nearby pain report: PASS")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)}/{len(tests)} feedback_engine tests passed.")
