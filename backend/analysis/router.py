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
Extract the complete PGN from this image.

Rules:
1. Output ONLY valid PGN. No explanations, no markdown, no code fences.
2. Include any visible PGN headers ([Event], [White], [Black], [Date], [Result] etc.)
3. Use standard SAN notation for all moves (e4, Nf3, O-O, Bxe5+, etc.)
4. Correct obvious handwriting errors using chess rules as a guide.
5. If a move is completely illegible, write a plausible legal move followed by {illegible}.
6. End with the result token (1-0 / 0-1 / 1/2-1/2 / *) if visible.

Output raw PGN only — no prose, no code fences."""


def _pdf_first_page_to_png(pdf_bytes: bytes) -> bytes:
    """Render the first page of a PDF to PNG bytes using PyMuPDF."""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise HTTPException(status_code=503, detail="PyMuPDF not installed — cannot process PDFs")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))  # 2× for better OCR
    return pix.tobytes("png")


def _strip_fences(text: str) -> str:
    """Remove accidental markdown code fences a model might add."""
    if "```" in text:
        lines = text.splitlines()
        text = "\n".join(ln for ln in lines if not ln.startswith("```")).strip()
    return text


async def _ocr_with_ollama(image_b64: str) -> str:
    """Call a local Ollama vision model and return the raw text response."""
    import httpx

    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": [{
            "role": "user",
            "content": _OCR_PROMPT,
            "images": [image_b64],   # Ollama expects raw base64, no data-URI prefix
        }],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


@router.post("/extract-pgn")
async def extract_pgn_from_image(file: UploadFile = File(...)):
    """
    Accept an image (PNG/JPEG/WEBP) or PDF, extract chess notation using a
    local Ollama vision model (e.g. llava), and return clean PGN text.

    Requires OLLAMA_BASE_URL to be set in .env (e.g. http://localhost:11434).
    This endpoint works locally only — Railway cannot reach a local Ollama.
    """
    # ── Guard: Ollama configured? ─────────────────────────────────────────────
    if not settings.ollama_base_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "OCR requires a local Ollama instance. "
                "Set OLLAMA_BASE_URL=http://localhost:11434 in your .env, "
                "and make sure you have pulled a vision model: ollama pull llava"
            ),
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

    # ── Encode to base64 ──────────────────────────────────────────────────────
    image_b64 = base64.standard_b64encode(raw).decode()

    # ── Call Ollama vision ────────────────────────────────────────────────────
    try:
        pgn_text = await _ocr_with_ollama(image_b64)
    except Exception as exc:
        logger.exception("Ollama OCR call failed")
        raise HTTPException(
            status_code=502,
            detail=f"Ollama error: {exc}. Is Ollama running? Does it have a vision model?",
        )

    return {"pgn": _strip_fences(pgn_text)}
