"""Game session model — one row per completed or in-progress game."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=5)        # Stockfish skill 1–20
    pgn: Mapped[str] = mapped_column(Text, default="")                 # full game in PGN
    result: Mapped[str] = mapped_column(String(20), default="in_progress")  # win/loss/draw/in_progress
    played_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
