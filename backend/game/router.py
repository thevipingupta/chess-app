"""Game vs computer — REST API."""

import json
import logging

import chess
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.jwt import get_current_user_id
from backend.database import get_db
from backend.game.engine import analyze_game, apply_moves, board_status, get_computer_move
from backend.game.schemas import (
    GameStateResponse,
    MoveRequest,
    MoveResponse,
    NewGameRequest,
    NewGameResponse,
)
from backend.models.game import GameSession

router = APIRouter(prefix="/game", tags=["game"])
logger = logging.getLogger(__name__)


@router.post("/new", response_model=NewGameResponse)
def new_game(
    req: NewGameRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Start a new game against the computer. Player is always White."""
    if not 1 <= req.difficulty <= 20:
        raise HTTPException(status_code=400, detail="Difficulty must be 1–20")

    session = GameSession(
        user_id=user_id,
        difficulty=req.difficulty,
        pgn="",           # stores space-separated UCI moves
        result="in_progress",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    starting_fen = chess.Board().fen()
    return NewGameResponse(game_id=session.id, fen=starting_fen)


@router.post("/{game_id}/move", response_model=MoveResponse)
def make_move(
    game_id: int,
    req: MoveRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Apply the player's move then get Stockfish's reply."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.result != "in_progress":
        raise HTTPException(status_code=400, detail=f"Game already ended: {session.result}")

    # Reconstruct board from stored moves
    moves = session.pgn.split() if session.pgn else []
    board = apply_moves(moves)

    # Validate and apply player's move
    try:
        player_move = chess.Move.from_uci(req.move)
        if player_move not in board.legal_moves:
            raise HTTPException(status_code=400, detail=f"Illegal move: {req.move}")
        board.push(player_move)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid move format: {req.move}")

    moves.append(req.move)

    # Check game state after player's move
    status, game_over, winner = board_status(board)
    computer_move_uci = None

    if not game_over:
        # Get Stockfish's reply
        try:
            computer_move_uci = get_computer_move(board.fen(), session.difficulty)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        if computer_move_uci:
            board.push_uci(computer_move_uci)
            moves.append(computer_move_uci)
            status, game_over, winner = board_status(board)

    # Persist updated game state
    session.pgn = " ".join(moves)
    if game_over:
        session.result = winner or "draw"
    db.commit()

    return MoveResponse(
        fen=board.fen(),
        player_move=req.move,
        computer_move=computer_move_uci,
        status=status,
        game_over=game_over,
        winner=winner,
    )


@router.get("/{game_id}/analyze")
def analyze(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Run Stockfish analysis on every player move after the game ends."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.result == "in_progress":
        raise HTTPException(status_code=400, detail="Game is still in progress")

    moves = session.pgn.split() if session.pgn else []
    if not moves:
        return {"game_id": game_id, "moves": [], "summary": {}}

    try:
        move_analysis = analyze_game(moves)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    counts = {"best": 0, "excellent": 0, "good": 0,
              "inaccuracy": 0, "mistake": 0, "blunder": 0}
    for m in move_analysis:
        counts[m["classification"]] += 1

    total = len(move_analysis)
    accuracy = round(
        100 * (counts["best"] + counts["excellent"] + counts["good"]) / total, 1
    ) if total else 0.0

    return {
        "game_id":  game_id,
        "moves":    move_analysis,
        "summary":  {**counts, "accuracy": accuracy, "total_moves": total},
    }


@router.get("/{game_id}", response_model=GameStateResponse)
def get_game(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return current game state."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")

    moves = session.pgn.split() if session.pgn else []
    board = apply_moves(moves)

    return GameStateResponse(
        game_id=session.id,
        fen=board.fen(),
        moves=moves,
        result=session.result,
        difficulty=session.difficulty,
    )
