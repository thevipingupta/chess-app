"""Chess puzzles — Module 2."""

import math
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.auth.jwt import get_current_user_id
from backend.models.puzzle import Puzzle, UserRating, PuzzleAttempt
from backend.puzzles.schemas import PuzzleOut, AttemptRequest, AttemptResponse, StatsOut

router = APIRouter(prefix="/puzzles", tags=["puzzles"])

K = 32  # Elo K-factor


def _expected(player: float, puzzle: float) -> float:
    return 1 / (1 + 10 ** ((puzzle - player) / 400))


def _elo_change(player: float, puzzle_rating: float, solved: bool) -> float:
    score = 1.0 if solved else 0.0
    expected = _expected(player, puzzle_rating)
    return round(K * (score - expected), 1)


def _get_or_create_rating(user_id: int, db: Session) -> UserRating:
    ur = db.query(UserRating).filter(UserRating.user_id == user_id).first()
    if not ur:
        ur = UserRating(user_id=user_id, rating=800.0, solved=0, attempted=0)
        db.add(ur)
        db.commit()
        db.refresh(ur)
    return ur


@router.get("/next", response_model=PuzzleOut)
def get_next_puzzle(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    ur = _get_or_create_rating(user_id, db)

    # Find a puzzle within ±200 rating of user, not yet attempted
    attempted_ids = db.query(PuzzleAttempt.puzzle_id).filter(
        PuzzleAttempt.user_id == user_id
    ).subquery()

    puzzle = (
        db.query(Puzzle)
        .filter(Puzzle.id.not_in(attempted_ids))
        .filter(Puzzle.rating >= ur.rating - 200)
        .filter(Puzzle.rating <= ur.rating + 200)
        .order_by(func.random())
        .first()
    )

    # If none in range, just pick any unattempted puzzle
    if not puzzle:
        puzzle = (
            db.query(Puzzle)
            .filter(Puzzle.id.not_in(attempted_ids))
            .order_by(func.random())
            .first()
        )

    # If all puzzles attempted, reset and pick random
    if not puzzle:
        puzzle = db.query(Puzzle).order_by(func.random()).first()

    if not puzzle:
        raise HTTPException(status_code=404, detail="No puzzles found. Run seed_puzzles.py first.")

    return PuzzleOut(
        puzzle_id=puzzle.id,
        fen=puzzle.fen,
        moves=puzzle.moves,
        rating=puzzle.rating,
        themes=puzzle.themes,
        user_rating=round(ur.rating, 1),
        solved_count=ur.solved,
        attempted_count=ur.attempted,
    )


@router.post("/{puzzle_id}/attempt", response_model=AttemptResponse)
def submit_attempt(
    puzzle_id: str,
    body: AttemptRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    puzzle = db.query(Puzzle).filter(Puzzle.id == puzzle_id).first()
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    ur = _get_or_create_rating(user_id, db)
    old_rating = ur.rating

    change = _elo_change(ur.rating, puzzle.rating, body.solved)
    ur.rating = max(400.0, ur.rating + change)   # floor at 400
    ur.attempted += 1
    if body.solved:
        ur.solved += 1

    attempt = PuzzleAttempt(
        user_id=user_id,
        puzzle_id=puzzle_id,
        solved=body.solved,
        time_taken_s=body.time_taken_s,
    )
    db.add(attempt)
    db.commit()

    return AttemptResponse(
        solved=body.solved,
        old_rating=round(old_rating, 1),
        new_rating=round(ur.rating, 1),
        rating_change=round(change, 1),
        solved_count=ur.solved,
        attempted_count=ur.attempted,
    )


@router.get("/stats", response_model=StatsOut)
def get_stats(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    ur = _get_or_create_rating(user_id, db)
    return StatsOut(rating=round(ur.rating, 1), solved=ur.solved, attempted=ur.attempted)
