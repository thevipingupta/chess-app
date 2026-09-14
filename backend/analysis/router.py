"""PGN analysis — REST API.

Accepts a raw PGN string, parses it with python-chess,
runs Stockfish on every move (both colours), and returns
per-move classifications plus headers.
"""

import logging
import chess
import chess.pgn
import chess.engine
import io

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.game.engine import _classify

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger(__name__)

TIME_PER_MOVE = 0.15   # seconds Stockfish thinks per move


class PgnRequest(BaseModel):
    pgn: str


@router.post("/pgn")
def analyze_pgn(req: PgnRequest):
    """Parse a PGN string and return Stockfish analysis for every move."""

    # ── 1. Parse PGN ──────────────────────────────────────────────────────────
    try:
        pgn_io = io.StringIO(req.pgn.strip())
        game = chess.pgn.read_game(pgn_io)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PGN parse error: {exc}")

    if game is None:
        raise HTTPException(status_code=400, detail="No game found in PGN")

    # Collect headers (White, Black, Date, Event, Result …)
    headers = dict(game.headers)

    # Collect (san, uci, side) for every move
    board = game.board()
    raw_moves = []
    for node in game.mainline():
        move = node.move
        san  = board.san(move)
        side = "white" if board.turn == chess.WHITE else "black"
        raw_moves.append({
            "san":  san,
            "uci":  move.uci(),
            "side": side,
            "fen_before": board.fen(),
        })
        board.push(move)
    # fen after last move
    final_fen = board.fen()

    if not raw_moves:
        return {"headers": headers, "moves": [], "final_fen": final_fen}

    # ── 2. Stockfish analysis ─────────────────────────────────────────────────
    analysed = []
    try:
        with chess.engine.SimpleEngine.popen_uci(settings.stockfish_path) as engine:
            replay = game.board()   # fresh board for analysis replay

            for i, m in enumerate(raw_moves):
                move_obj = chess.Move.from_uci(m["uci"])

                # Eval BEFORE
                info_pre = engine.analyse(replay, chess.engine.Limit(time=TIME_PER_MOVE))
                score_pre_obj = info_pre["score"].white()
                score_pre = score_pre_obj.score(mate_score=10000)

                # Best move in position
                pv = info_pre.get("pv") or []
                best_uci = pv[0].uci() if pv else m["uci"]
                # Convert best to SAN (before pushing)
                try:
                    best_san = replay.san(chess.Move.from_uci(best_uci))
                except Exception:
                    best_san = best_uci

                # Apply move
                replay.push(move_obj)
                fen_after = replay.fen()

                # Eval AFTER
                info_post = engine.analyse(replay, chess.engine.Limit(time=TIME_PER_MOVE))
                score_post_obj = info_post["score"].white()
                score_post = score_post_obj.score(mate_score=10000)

                # For black moves centipawn loss is from black's POV
                if m["side"] == "white":
                    cp_loss = max(0, score_pre - score_post) if (score_pre is not None and score_post is not None) else 0
                else:
                    cp_loss = max(0, score_post - score_pre) if (score_pre is not None and score_post is not None) else 0

                is_best = (best_uci == m["uci"])
                classification = _classify(cp_loss, is_best)

                # Mate detection for display
                def fmt_score(s_obj):
                    m_val = s_obj.mate()
                    if m_val is not None:
                        return f"M{m_val}" if m_val > 0 else f"M{m_val}"
                    cp = s_obj.score()
                    return cp if cp is not None else 0

                analysed.append({
                    "move_num":       (i // 2) + 1,
                    "side":           m["side"],
                    "san":            m["san"],
                    "uci":            m["uci"],
                    "fen_before":     m["fen_before"],
                    "fen_after":      fen_after,
                    "classification": classification,
                    "cp_loss":        cp_loss,
                    "best_move":      best_san,
                    "is_best":        is_best,
                    "eval_before":    fmt_score(score_pre_obj),
                    "eval_after":     fmt_score(score_post_obj),
                })

    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Stockfish not found — check STOCKFISH_PATH in .env")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analysis failed: {exc}")

    return {
        "headers":   headers,
        "moves":     analysed,
        "final_fen": final_fen,
    }
