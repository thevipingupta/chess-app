"""Pydantic schemas for game endpoints."""

from pydantic import BaseModel


class NewGameRequest(BaseModel):
    difficulty: int = 5          # Stockfish skill level 1–20


class NewGameResponse(BaseModel):
    game_id: int
    fen: str                     # starting position
    turn: str = "white"          # player always plays white


class MoveRequest(BaseModel):
    move: str                    # UCI format e.g. "e2e4"
    coach: bool = False          # Coach Mode — narrate this move via Ollama


class MoveResponse(BaseModel):
    fen: str                     # board after both moves
    player_move: str             # the move you made (UCI)
    computer_move: str | None    # computer's reply (UCI), None if game over
    status: str                  # "ok" | "check" | "checkmate" | "stalemate" | "draw"
    game_over: bool
    winner: str | None           # "white" | "black" | "draw" | None
    coach_player: str | None = None    # narrated feedback on your move (Coach Mode)


class GameStateResponse(BaseModel):
    game_id: int
    fen: str
    moves: list[str]             # full move list in UCI
    result: str
    difficulty: int
