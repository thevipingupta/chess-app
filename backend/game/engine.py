"""Stockfish engine wrapper using python-chess.

Opens and closes the engine per call — safe for multi-user use.
"""

import logging
import chess
import chess.engine

from backend.config import settings

logger = logging.getLogger(__name__)


def get_computer_move(fen: str, skill_level: int = 5) -> str | None:
    """Return Stockfish's best move for the given position.

    Args:
        fen: Current board position in FEN notation.
        skill_level: Stockfish skill 1 (weakest) to 20 (strongest).

    Returns:
        Move in UCI notation (e.g. "e7e5"), or None if no legal moves.
    """
    board = chess.Board(fen)
    if board.is_game_over():
        return None

    try:
        with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
            engine.configure({"Skill Level": skill_level})
            # Think time scales with difficulty — harder = more time
            think_ms = 100 + skill_level * 50   # 150ms (lvl1) → 1100ms (lvl20)
            result = engine.play(board, chess.engine.Limit(time=think_ms / 1000))
            return result.move.uci() if result.move else None
    except FileNotFoundError:
        logger.error("Stockfish binary not found at: %s", settings.stockfish_path)
        raise RuntimeError(
            f"Stockfish not found at {settings.stockfish_path}. "
            "Download from https://stockfishchess.org/download/ and update STOCKFISH_PATH in .env"
        )


def board_status(board: chess.Board) -> tuple[str, bool, str | None]:
    """Return (status, game_over, winner) for the current board state.

    status  : "ok" | "check" | "checkmate" | "stalemate" | "draw"
    winner  : "white" | "black" | "draw" | None
    """
    if board.is_checkmate():
        # The side that just moved wins
        winner = "black" if board.turn == chess.WHITE else "white"
        return "checkmate", True, winner
    if board.is_stalemate():
        return "stalemate", True, "draw"
    if board.is_insufficient_material() or board.is_seventyfive_moves() or board.is_fivefold_repetition():
        return "draw", True, "draw"
    if board.is_check():
        return "check", False, None
    return "ok", False, None


def apply_moves(moves: list[str]) -> chess.Board:
    """Reconstruct a Board by replaying a list of UCI moves from the start."""
    board = chess.Board()
    for uci in moves:
        board.push_uci(uci)
    return board


def _classify(cp_loss: int, is_best: bool) -> str:
    if is_best or cp_loss <= 0:
        return "best"
    if cp_loss <= 10:
        return "excellent"
    if cp_loss <= 30:
        return "good"
    if cp_loss <= 100:
        return "inaccuracy"
    if cp_loss <= 300:
        return "mistake"
    return "blunder"


def analyze_game(moves: list[str], time_per_move: float = 0.15) -> list[dict]:
    """Analyse every move (both colours) using Stockfish.

    Returns a list of dicts — one per ply — with side, classification,
    centipawn loss, best move suggestion, and eval before/after.
    """
    board = chess.Board()
    results = []

    try:
        with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
            for i, uci in enumerate(moves):
                move = chess.Move.from_uci(uci)
                side = "white" if i % 2 == 0 else "black"

                # Eval BEFORE the move (always from White's POV)
                info_pre = engine.analyse(board, chess.engine.Limit(time=time_per_move))
                score_pre = info_pre["score"].white().score(mate_score=10000)
                pv = info_pre.get("pv") or []
                best_uci = pv[0].uci() if pv else uci

                board.push(move)

                # Eval AFTER the move
                info_post = engine.analyse(board, chess.engine.Limit(time=time_per_move))
                score_post = info_post["score"].white().score(mate_score=10000)

                if score_pre is None or score_post is None:
                    cp_loss = 0
                elif side == "white":
                    cp_loss = max(0, score_pre - score_post)
                else:
                    cp_loss = max(0, score_post - score_pre)

                is_best = (best_uci == uci)

                results.append({
                    "move_num":       (i // 2) + 1,
                    "side":           side,
                    "uci":            uci,
                    "best_move":      best_uci,
                    "is_best":        is_best,
                    "cp_loss":        cp_loss,
                    "classification": _classify(cp_loss, is_best),
                    "eval_before":    score_pre,
                    "eval_after":     score_post,
                })

    except FileNotFoundError:
        logger.error("Stockfish binary not found at: %s", settings.stockfish_path)
        raise RuntimeError(
            f"Stockfish not found at {settings.stockfish_path}. "
            "Download from https://stockfishchess.org/download/ and update STOCKFISH_PATH in .env"
        )

    return results
