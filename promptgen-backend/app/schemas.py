from pydantic import BaseModel


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


class FeedbackSubmission(BaseModel):
    entries: list[FeedbackEntry]
