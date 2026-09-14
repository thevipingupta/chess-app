"""Puzzle library and per-user progression models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Puzzle(Base):
    """One row per Lichess puzzle (imported from CSV)."""

    __tablename__ = "puzzles"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)   # Lichess puzzle id
    fen: Mapped[str] = mapped_column(Text, nullable=False)           # starting position
    moves: Mapped[str] = mapped_column(Text, nullable=False)         # correct move sequence (UCI)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)     # difficulty rating
    themes: Mapped[str] = mapped_column(Text, default="")            # e.g. "fork pin mateIn2"


class UserRating(Base):
    """One row per user — their current puzzle rating."""

    __tablename__ = "user_ratings"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    rating: Mapped[float] = mapped_column(Float, default=800.0)      # starts at 800 (beginner)
    solved: Mapped[int] = mapped_column(Integer, default=0)
    attempted: Mapped[int] = mapped_column(Integer, default=0)


class PuzzleAttempt(Base):
    """One row per puzzle attempt by a user."""

    __tablename__ = "puzzle_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    puzzle_id: Mapped[str] = mapped_column(String(20), ForeignKey("puzzles.id"), nullable=False)
    solved: Mapped[bool] = mapped_column(Boolean, default=False)
    time_taken_s: Mapped[int] = mapped_column(Integer, default=0)    # seconds
    attempted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
