from pydantic import BaseModel


# ── Member auth (login_code only) ────────────────────────────────────────────
# A single step, scoped by (gym, login_code) — see main.py's POST
# /member/login. The 8-digit code (gym-issued, from gym-dashboard's "Add
# Member" flow) is the only credential; there is no member password anymore.
class MemberLoginRequest(BaseModel):
    code: str
    gym: str | None = None


class GenerateRequest(BaseModel):
    prompt: str
    system: str | None = None
    bmi: float | None = None


class GenerateResponse(BaseModel):
    result: str


class FeedbackEntry(BaseModel):
    day_index: int
    day_name: str
    exercise: str
    set_number: int
    weight_kg: float | None = None
    difficulty: int | None = None  # 1-5 star rating the user gave THIS set
    # ── Phase 6 additions: needed for objective progression math. All
    # optional so any existing caller keeps working unmodified until it's
    # updated to send them.
    reps_completed:   int | None = None
    target_reps:      int | None = None
    target_weight_kg: float | None = None
    completed:        bool = True


class FeedbackSubmission(BaseModel):
    entries: list[FeedbackEntry]


# ── Workout set/exercise feedback (used by Templates/result.html) ──────────
# Previously written directly from the browser to Supabase (workout_set_
# feedback / workout_exercise_feedback tables) using the member's Supabase
# Auth session + RLS keyed on auth.uid(). Now that members don't get a
# Supabase Auth user at all (see auth.py), that path is gone — these two
# tables are read/written through the backend instead, scoped to
# member["id"] from the verified session token, same as everything else.
class SetFeedbackEntry(BaseModel):
    day_index: int
    day_name: str | None = None
    exercise: str
    set_number: int
    weight_kg: float | None = None
    reps_used: int | None = None
    cycle_number: int = 1


class ExerciseFeedbackEntry(BaseModel):
    day_index: int
    day_name: str | None = None
    exercise: str
    difficulty: int | None = None
    notes: str | None = None
    cycle_number: int = 1


class SetFeedbackSubmission(BaseModel):
    entries: list[SetFeedbackEntry]


class ExerciseFeedbackSubmission(BaseModel):
    entries: list[ExerciseFeedbackEntry]
class ReadinessCheckinSubmission(BaseModel):
    day_index:    int
    rating:       int   # 1-5, pre-session self-report
    notes:        str | None = None
    cycle_number: int = 1


class CheckinSubmission(BaseModel):
    recovery:   str   # excellent | good | average | poor
    difficulty: str   # too_easy | easy | just_right | hard | too_hard
    soreness:   str   # none | mild | moderate | severe
    pain_areas: list[str] = []   # shoulder, elbow, wrist, lower_back, hip, knee, ankle, other
    pain_notes: str | None = None

    # Optional body measurements — program must continue normally if skipped.
    body_weight_kg: float | None = None
    waist_cm:       float | None = None
    chest_cm:       float | None = None
    arms_cm:        float | None = None
    thighs_cm:      float | None = None
    hips_cm:        float | None = None
    body_fat_pct:   float | None = None
