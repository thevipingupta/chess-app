"""PGN analysis — REST API.

Accepts a raw PGN string, parses it with python-chess,
runs Stockfish on every move (both colours), and returns
per-move classifications plus headers.

Also provides an OCR endpoint that accepts an image or PDF,
extracts chess notation using Claude Vision, and returns clean PGN.
"""

import base64
import logging
import chess
import chess.pgn
import chess.engine
import io

from fastapi import APIRouter, HTTPException, UploadFile, File
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


# ── OCR endpoint ──────────────────────────────────────────────────────────────

_ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "application/pdf",
}

_OCR_PROMPT = """\
You are a chess scoresheet transcription expert.

The image shows a chess scoresheet, PGN printout, or handwritten game notation.
Your job is to extract the complete PGN from this image.

Rules:
1. Output ONLY valid PGN. No explanations, no markdown, no code fences.
2. Include any visible PGN headers ([Event], [White], [Black], [Date], [Result] etc.)
3. Use standard SAN notation for all moves (e4, Nf3, O-O, Bxe5+, etc.)
4. Correct obvious handwriting errors — e.g. a letter that looks like both 'R' and 'P'
   should be resolved based on what's legal in that position.
5. If a move is completely illegible, substitute a plausible legal move and append
   a comment after it: {illegible}.
6. End with the result token (1-0 / 0-1 / 1/2-1/2 / *) if visible.

Output raw PGN only."""


def _image_bytes_to_b64(data: bytes, media_type: str) -> tuple[str, str]:
    """Return (base64_string, media_type) ready for the Anthropic Vision API."""
    return base64.standard_b64encode(data).decode(), media_type


def _pdf_first_page_to_png(pdf_bytes: bytes) -> bytes:
    """Render the first page of a PDF to PNG bytes using PyMuPDF."""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise HTTPException(status_code=503, detail="PyMuPDF not installed — cannot process PDFs")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    # 2× resolution for better OCR accuracy
    mat = fitz.Matrix(2.0, 2.0)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


@router.post("/extract-pgn")
async def extract_pgn_from_image(file: UploadFile = File(...)):
    """
    Accept an image (PNG/JPEG/WEBP) or PDF, extract chess notation via
    Claude Vision, and return clean PGN text.
    """
    # ── Guard: API key configured? ────────────────────────────────────────────
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not configured. Add it in Railway → Variables.",
        )

    # ── Guard: file type ──────────────────────────────────────────────────────
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Upload PNG, JPEG, WEBP, or PDF.",
        )

    # ── Read file ─────────────────────────────────────────────────────────────
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:  # 20 MB cap
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")

    # ── PDF → PNG ─────────────────────────────────────────────────────────────
    if content_type == "application/pdf":
        raw = _pdf_first_page_to_png(raw)
        content_type = "image/png"

    # ── Normalise JPEG content-type ───────────────────────────────────────────
    if content_type == "image/jpg":
        content_type = "image/jpeg"

    # ── Call Claude Vision ────────────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        b64_data, media_type = _image_bytes_to_b64(raw, content_type)

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        },
                        {"type": "text", "text": _OCR_PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:
        logger.exception("Claude Vision call failed")
        raise HTTPException(status_code=502, detail=f"Vision API error: {exc}")

    pgn_text = message.content[0].text.strip()

    # Strip accidental markdown code fences if the model adds them
    if pgn_text.startswith("```"):
        lines = pgn_text.splitlines()
        pgn_text = "\n".join(
            ln for ln in lines if not ln.startswith("```")
        ).strip()

    return {"pgn": pgn_text}
