"""Game vs computer — REST API."""

import json
import logging

import chess
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.jwt import get_current_user_id
from backend.config import settings
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


@router.post("/{game_id}/resign")
def resign(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Player resigns — computer wins."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.result != "in_progress":
        raise HTTPException(status_code=400, detail="Game is already over")

    session.result = "black"
    db.commit()
    return {"game_over": True, "winner": "black"}


@router.post("/{game_id}/draw-offer")
def draw_offer(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Player offers a draw. Computer accepts if position is roughly equal (≤100cp)."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.result != "in_progress":
        raise HTTPException(status_code=400, detail="Game is already over")

    moves = session.pgn.split() if session.pgn else []
    board = apply_moves(moves)

    try:
        with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
            info = engine.analyse(board, chess.engine.Limit(time=0.1))
            score = info["score"].white().score(mate_score=10000)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Stockfish not available")

    if score is None or abs(score) <= 100:
        session.result = "draw"
        db.commit()
        return {"accepted": True, "message": "Computer accepts the draw — well played!"}
    elif score > 0:
        return {"accepted": False, "message": "Computer declines — it's winning and wants to play on!"}
    else:
        return {"accepted": False, "message": "Computer declines — keep fighting, you might turn it around!"}


@router.get("/{game_id}/hint")
def get_hint(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return Stockfish's best move for the current position as a hint."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.result != "in_progress":
        raise HTTPException(status_code=400, detail="Game is already over")
    if session.difficulty > 10:
        raise HTTPException(status_code=400, detail="Hints not available at this difficulty")

    moves = session.pgn.split() if session.pgn else []
    board = apply_moves(moves)

    try:
        with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
            result = engine.play(board, chess.engine.Limit(time=0.4))
        if not result.move:
            raise HTTPException(status_code=400, detail="No hint available")
        uci = result.move.uci()
        return {"from": uci[:2], "to": uci[2:4]}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Stockfish not found — check server config")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Hint failed: {exc}")


@router.post("/{game_id}/takeback")
def takeback(
    game_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Undo the last player + computer move pair. Only allowed at difficulty ≤ 10."""
    session = db.query(GameSession).filter(
        GameSession.id == game_id, GameSession.user_id == user_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Game not found")
    if session.difficulty > 10:
        raise HTTPException(status_code=400, detail="Take back is only available at Beginner, Casual, and Intermediate levels")

    moves = session.pgn.split() if session.pgn else []
    if not moves:
        raise HTTPException(status_code=400, detail="No moves to take back")

    # Remove last 2 moves (player + computer reply), or 1 if only player moved
    moves = moves[:-2] if len(moves) >= 2 else []
    board = apply_moves(moves)

    session.pgn = " ".join(moves)
    session.result = "in_progress"   # restore if game was already over
    db.commit()

    return {"fen": board.fen(), "move_count": len(moves)}


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
