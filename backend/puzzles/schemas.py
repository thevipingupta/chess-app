from pydantic import BaseModel


class PuzzleOut(BaseModel):
    puzzle_id: str
    fen: str
    moves: str          # full solution (space-separated UCI) — sent so frontend can validate locally
    rating: int
    themes: str
    user_rating: float
    solved_count: int
    attempted_count: int


class AttemptRequest(BaseModel):
    solved: bool
    time_taken_s: int = 0


class AttemptResponse(BaseModel):
    solved: bool
    old_rating: float
    new_rating: float
    rating_change: float
    solved_count: int
    attempted_count: int


class StatsOut(BaseModel):
    rating: float
    solved: int
    attempted: int
