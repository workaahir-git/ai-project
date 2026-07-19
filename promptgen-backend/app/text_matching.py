"""
text_matching.py — shared, dependency-free keyword-matching helper.

Extracted out of progression_engine.py during the Engine 40/43 integration
audit, once the same negation-false-positive bug ("no pain" matching as a
pain report) turned up in THREE places: progression_engine.py's own pain
check, feedback_engine.py (Engine 43, which unions progression_engine's
list with its own), and exercise_database.py's injury-keyword parser.

This module has ZERO imports from anywhere else in this app on purpose —
progression_engine.py already imports FROM exercise_database.py
(get_substitutes_for_exercise), so exercise_database.py importing back from
progression_engine.py would be a circular import. Putting the shared logic
here, with no dependencies in either direction, avoids that entirely.
"""

from __future__ import annotations

# Deliberately simple, not a full negation-scope parser — just enough to
# catch "no X" / "not X" / "without X" immediately before a keyword.
NEGATION_PRECEDERS = ("no ", "not ", "n't", "without ", "zero ", "never ")


def text_has_unnegated_keyword(text: str, keywords: tuple) -> bool:
    """text must already be lowercased. Checks each keyword occurrence for
    a negation word in the ~12 characters immediately before it; if every
    occurrence of every keyword is negated, returns False.
    """
    for kw in keywords:
        idx = text.find(kw)
        while idx != -1:
            preceding = text[max(0, idx - 12):idx]
            if not any(neg in preceding for neg in NEGATION_PRECEDERS):
                return True
            idx = text.find(kw, idx + 1)
    return False
