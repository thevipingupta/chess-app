"""Seed chess puzzles. Every position is built by replaying moves with python-chess
so all FENs and solution moves are guaranteed legal.

Run:  uv run python seed_puzzles.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import chess
from backend.database import SessionLocal, engine, Base
from backend.models.puzzle import Puzzle

Base.metadata.create_all(bind=engine)


def build(setup: str, solution: str):
    """Play setup moves from start, verify solution moves are legal, return (fen, solution)."""
    board = chess.Board()
    for m in setup.split():
        move = chess.Move.from_uci(m)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal setup move: {m} in pos {board.fen()}")
        board.push(move)
    fen = board.fen()
    b = board.copy()
    for m in solution.split():
        move = chess.Move.from_uci(m)
        if move not in b.legal_moves:
            raise ValueError(f"Illegal solution move: {m} in pos {b.fen()}")
        b.push(move)
    return fen, solution


# (id, setup_from_start, solution_moves, rating, themes)
PUZZLES = [
    # ── Mate in 1 ──────────────────────────────────────────────────────────────
    ("puz01", "f2f3 e7e5 g2g4",
     "d8h4",
     500, "mateIn1"),

    ("puz02", "e2e4 e7e5 d1h5 b8c6 f1c4 g8f6",
     "h5f7",
     550, "mateIn1"),

    ("puz03", "e2e4 e7e5 f1c4 d7d6 d1h5 g7g6",
     "h5e5",
     520, "mateIn1 fork"),

    # ── Mate in 2 ──────────────────────────────────────────────────────────────
    ("puz04", "e2e4 e7e5 f1c4 b8c6 d1h5 g8f6 h5f7 e8e7",
     "h2h4 e7d6 h4h5",
     800, "mateIn2 attack"),

    # ── Forks ──────────────────────────────────────────────────────────────────
    ("puz05", "e2e4 e7e5 g1f3 b8c6 f1c4 g8f6 b1c3 f8c5 e1g1 e8g8 d2d3 d7d6 c1g5 h7h6 g5f6 d8f6",
     "c3d5 f6d8 d5c7",
     900, "fork"),

    ("puz06", "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f3d4 g8f6 d4c6 b7c6 e4e5 d8e7 d1e2 f6d5 c2c4 d5b4",
     "a2a3 b4c2 d1c2",
     750, "fork"),

    # ── Pins ───────────────────────────────────────────────────────────────────
    ("puz07", "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5c6 d7c6 e1g1 f8d6 d2d4 e5d4 f3d4 g8f6 b1c3 e8g8",
     "c1g5 f6e4 g5d8 e4c3 d8d6 c3a2",
     1000, "pin combination"),

    ("puz08", "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8 g1f3 h7h6 g5h4 b7b6 c4d5 e6d5 f1b5 c8b7",
     "h4f6 e7f6 c3d5 f6d4 d5b4",
     1100, "pin exchange"),

    # ── Discovered attacks ─────────────────────────────────────────────────────
    ("puz09", "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f3d4 f8c5 c1e3 d8f6 c2c3 g8e7 f1e2 e8g8 e1g1",
     "c5d4 e3d4 c6d4 d1d4 f6d4 c3d4",
     850, "discovered"),

    # ── Back-rank ──────────────────────────────────────────────────────────────
    ("puz10", "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6 e5f3 f6e4 d2d4 d6d5 f1d3 b8c6 e1g1 f8e7 h2h3 e8g8 c2c4 c6b4 d3e2 d5c4 e2c4 b4c6",
     "d4d5 c6e5 f3e5 f7f6 e5d3 d8d5 c4d5",
     1050, "backRank"),

    # ── Intermediate combos ─────────────────────────────────────────────────────
    ("puz11", "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 a7a6 f1e2 e7e5 d4b3 f8e7 e1g1 e8g8 f2f3 b8c6 c1e3 c6d4 b3d4 e5d4 e3d4 f6e8",
     "d4g7 e8g7 d1d6 e7d6 f1f8",
     1200, "combination sacrifice"),

    ("puz12", "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5c6 d7c6 e1g1 f8d6 d2d3 g8f6 b1d2 e8g8 d2c4 d6c7 c1g5 h7h6 g5f6 d8f6 c4e5 f6e5 f3e5 c7e5",
     "d3d4 e5d6 d4d5 c6d5 f1d1",
     1150, "endgame"),

    ("puz13", "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8 g1f3 b8d7 d1c2 c7c6 f1d3 h7h6 g5f4 d7h3",
     "g2h3 f6h5 f4d2 e7h4 d2f3 h5f4 e3f4",
     1300, "combination"),

    # ── Advanced ───────────────────────────────────────────────────────────────
    ("puz14", "e2e4 e7e5 g1f3 g8f6 f1c4 f6e4 d1d5 e4d6 c4b3 f8e7 b1c3 e8g8 c1g5 e7f6 g5f6 d8f6 d5e5 f6e5 f3e5 b8c6 e5d3",
     "c6d4 d3f4 d4b3 a2b3 d7d5",
     1100, "endgame strategy"),

    ("puz15", "d2d4 g8f6 c2c4 g7g6 g2g3 f8g7 f1g2 e8g8 b1c3 d7d6 g1f3 b8d7 e1g1 e7e5 e2e4 c7c6 h2h3 d8b6",
     "d4e5 d6e5 d1d8 f8d8 f3e5 d7e5 c3b5",
     1400, "endgame"),

    # ── Knight tours ───────────────────────────────────────────────────────────
    ("puz16", "e2e4 e7e5 b1c3 b8c6 f1c4 f8c5 d1h5 g8f6 h5f7 e8f8 f7d5 d8e7 d5c5 e7c5 g1f3",
     "c5g1 h1g1 d7d5 c4d5 f6d5 c3d5 c6b4",
     950, "combination"),

    ("puz17", "e2e4 c7c5 b1c3 b8c6 g2g3 g7g6 f1g2 f8g7 d2d3 d7d6 g1e2 e7e5 e1g1 g8e7 c1e3 e8g8 d1d2",
     "c6d4 e2d4 e5d4 e3d4 e7c6 d4c3",
     1050, "strategy"),

    ("puz18", "d2d4 d7d5 g1f3 g8f6 c2c4 e7e6 b1c3 f8e7 c1f4 e8g8 e2e3 c7c5 d4c5 e7c5 a2a3 b8c6 d1d2 d8e7",
     "c4d5 e6d5 c3b5 e7d8 b5d6 f8e8 f3e5",
     1250, "strategy endgame"),

    ("puz19", "e2e4 e7e5 g1f3 g8f6 b1c3 b8c6 f1b5 f8b4 e1g1 e8g8 d2d3 d7d6 c1g5 b4c3 b2c3 d8e7 h2h3 c6e5 f3e5 e7e5",
     "g5f6 e5f6 b5c4 e8h5 c4f7 h8d8 f3d5",
     1300, "attack"),

    ("puz20", "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4 f3d4 g7g6 c2c4 f8g7 b1e3 g8f6 b1c3 e8g8 f1e2 d7d6 e1g1 c8d7",
     "d4c6 d7c6 e3d5 f6d5 c4d5 c6b5 e2b5",
     1200, "strategy"),

    ("puz21", "e2e4 e7e5 g1f3 g8f6 f3e5 d7d6 e5f3 f6e4 f1c4 f8c5 e1g1 e8g8 d2d4 e4f6 d4c5 d6c5 b1c3 b8c6",
     "c3d5 f6d5 c4d5 c6d4 d5f7 f8f7 f3d4",
     1150, "combination"),

    ("puz22", "d2d4 d7d5 c2c4 c7c6 g1f3 g8f6 b1c3 e7e6 c1g5 h7h6 g5f6 d8f6 e2e3 b8d7 d1c2 g7g6 f1d3 f8g7 e1g1",
     "e8g8 h2h4 f8e8 h4h5 g6h5 f3e5",
     1100, "attack"),

    ("puz23", "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3 c6a5 b3c2",
     "c7c5 d2d4 d8c7 b1d2 c5d4 c3d4 a5c6",
     1000, "strategy"),

    ("puz24", "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 a7a6 c1e3 e7e5 d4b3 f8e7 f2f3 e8g8 d1d2 b8d7 g2g4",
     "d6d5 g4g5 f6h5 e4d5 d7c5 b3c5 e7c5",
     1350, "attack strategy"),

    ("puz25", "e2e4 e7e5 f2f4 e5f4 g1f3 g7g5 f1c4 g5g4 e1g1 g4f3 d1f3 d8f6 e4e5 f6e5 f1e1 e5f6 c4f7 e8d8",
     "f3d5 c7c6 d5d8",
     1200, "sacrifice combination"),

    ("puz26", "d2d4 f7f5 e2e4 f5e4 b1c3 g8f6 c1g5 e7e6 d1d2 f8e7 e1c1 e8g8 f1c4 d7d5 c4b3 c7c6 g1h3 b8d7",
     "h3g5 h7h6 g5e6 d8e8 e6g7",
     1300, "attack"),

    ("puz27", "e2e4 e7e5 g1f3 g8f6 d2d4 f6e4 f1d3 d7d5 f3e5 b8d7 e5d7 c8d7 e1g1 f8d6 c2c4 e4f6 b1c3 e8g8",
     "c4d5 d7g4 d1d2 f6d5 c3d5 d8d5 d3e4",
     1150, "strategy"),

    ("puz28", "e2e4 c7c6 d2d4 d7d5 b1c3 d5e4 c3e4 b8d7 g1f3 g8f6 e4f6 d7f6 f1d3 c8g4 e1g1 e7e6 c2c3 f8d6",
     "d1e2 d8c7 f3e5 g4d1 e5f7 e8d8 f7d6",
     1400, "combination sacrifice"),

    ("puz29", "e2e4 e7e5 g1f3 b8c6 f1b5 g8f6 e1g1 f6e4 f1e1 e4d6 f3e5 f8e7 b5f1 c6e5 e1e5 e8g8 d2d4 e7f6",
     "e5e1 d6f5 d4d5 f6d4 c2c3 d4f6 e1f1",
     1200, "endgame rook"),

    ("puz30", "d2d4 d7d5 c2c4 c7c6 b1c3 g8f6 e2e3 e7e6 g1f3 b8d7 d1c2 f8d6 f1d3 e8g8 e1g1 f8e8 e3e4 e6e5",
     "c4d5 c6d5 e4d5 e5d4 c3e4 f6e4 d3e4",
     1050, "center strategy"),
]


def main():
    db = SessionLocal()
    try:
        added, skipped, errors = 0, 0, 0

        for pid, setup, solution, rating, themes in PUZZLES:
            if db.query(Puzzle).filter(Puzzle.id == pid).first():
                skipped += 1
                continue
            try:
                fen, moves = build(setup, solution)
                db.add(Puzzle(id=pid, fen=fen, moves=moves, rating=rating, themes=themes))
                added += 1
            except (ValueError, Exception) as e:
                print(f"  ⚠️  {pid} skipped: {e}")
                errors += 1

        db.commit()
        total = db.query(Puzzle).count()
        print(f"\n✅ Done — added {added}, skipped {skipped}, errors {errors}")
        print(f"   Total puzzles in DB: {total}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
