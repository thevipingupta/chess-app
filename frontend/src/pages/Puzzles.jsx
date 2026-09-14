import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import api from "../api";
import { playMoveSound, playOpponentMoveSound } from "../utils/sounds";

export default function Puzzles() {
  const navigate  = useNavigate();
  const gameRef   = useRef(null);
  const solRef    = useRef([]);
  const idxRef    = useRef(0);
  const pidRef    = useRef(null);
  const startRef  = useRef(null);

  const [fen, setFen]               = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  const [status, setStatus]         = useState("idle");
  const [message, setMessage]       = useState("Loading puzzle…");
  const [puzzleRating, setPuzzleRating] = useState(null);
  const [themes, setThemes]         = useState("");
  const [userRating, setUserRating] = useState(null);
  const [ratingChange, setRatingChange] = useState(null);
  const [stats, setStats]           = useState({ solved: 0, attempted: 0 });
  const [loading, setLoading]       = useState(false);

  // Click-to-move state
  const [selectedSq, setSelectedSq]       = useState(null);
  const [optionSquares, setOptionSquares] = useState({});

  // ── Load next puzzle ──────────────────────────────────────────────────────
  const loadPuzzle = useCallback(async () => {
    setLoading(true);
    setRatingChange(null);
    setSelectedSq(null);
    setOptionSquares({});
    try {
      const { data } = await api.get("/puzzles/next");
      const chess = new Chess(data.fen);
      const moves = data.moves.trim().split(/\s+/);

      gameRef.current  = chess;
      solRef.current   = moves;
      idxRef.current   = 0;
      pidRef.current   = data.puzzle_id;
      startRef.current = Date.now();

      setFen(chess.fen());
      setPuzzleRating(data.rating);
      setThemes(data.themes);
      setUserRating(data.user_rating);
      setStats({ solved: data.solved_count, attempted: data.attempted_count });
      setStatus("playing");
      const turn = chess.turn() === "w" ? "White ♙" : "Black ♟";
      setMessage(`You play ${turn} — click a piece then click where to move`);
    } catch (e) {
      setMessage(e.response?.data?.detail || "Failed to load puzzle");
      setStatus("idle");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadPuzzle(); }, [loadPuzzle]);

  // ── Submit result ─────────────────────────────────────────────────────────
  const submitResult = useCallback(async (solved) => {
    if (!pidRef.current) return;
    const timeTaken = Math.floor((Date.now() - startRef.current) / 1000);
    try {
      const { data } = await api.post(`/puzzles/${pidRef.current}/attempt`, {
        solved, time_taken_s: timeTaken,
      });
      setUserRating(data.new_rating);
      setRatingChange(data.rating_change);
      setStats({ solved: data.solved_count, attempted: data.attempted_count });
    } catch {}
  }, []);

  // ── Core move logic (shared by click and drag) ────────────────────────────
  const attemptMove = useCallback((from, to, promotion = "q") => {
    if (status !== "playing") return false;
    const chess = gameRef.current;
    if (!chess) return false;

    const expectedUCI = solRef.current[idxRef.current];
    if (!expectedUCI) return false;

    let moveResult;
    try {
      moveResult = chess.move({ from, to, promotion });
    } catch { return false; }
    if (!moveResult) return false;

    const playedUCI = moveResult.from + moveResult.to + (moveResult.promotion || "");
    const expNorm   = expectedUCI.slice(0, 4) + (expectedUCI[4] || "");

    if (playedUCI !== expNorm) {
      chess.undo();
      setStatus("wrong");
      setMessage("❌ Not the best move. Try again or reveal the solution.");
      submitResult(false);
      return false;
    }

    playMoveSound();
    setFen(chess.fen());
    setSelectedSq(null);
    setOptionSquares({});
    const nextIdx = idxRef.current + 1;

    if (nextIdx >= solRef.current.length) {
      idxRef.current = nextIdx;
      setStatus("correct");
      setMessage("✅ Brilliant! Puzzle solved!");
      submitResult(true);
      return true;
    }

    // Auto-play opponent response
    const oppUCI = solRef.current[nextIdx];
    idxRef.current = nextIdx + 1;
    setTimeout(() => {
      try {
        chess.move({ from: oppUCI.slice(0,2), to: oppUCI.slice(2,4), promotion: oppUCI[4] || undefined });
        playOpponentMoveSound();
        setFen(chess.fen());
        if (idxRef.current >= solRef.current.length) {
          setStatus("correct");
          setMessage("✅ Brilliant! Puzzle solved!");
          submitResult(true);
        } else {
          setMessage("Good move! Keep going…");
        }
      } catch {}
    }, 500);

    return true;
  }, [status, submitResult]);

  // ── Drag-and-drop handler ─────────────────────────────────────────────────
  const onDrop = useCallback((from, to, piece) => {
    const promo = piece?.[1] === "P" && (to[1] === "8" || to[1] === "1")
      ? (solRef.current[idxRef.current]?.[4] || "q") : "q";
    return attemptMove(from, to, promo);
  }, [attemptMove]);

  // ── Click-to-move handler ─────────────────────────────────────────────────
  // react-chessboard v5 passes { square, piece } object (not a plain string)
  const onSquareClick = useCallback(({ square }) => {
    if (status !== "playing") return;
    const chess = gameRef.current;
    if (!chess) return;

    // If a square is already selected, try to move there
    if (selectedSq) {
      const moved = attemptMove(selectedSq, square);
      if (!moved) {
        // Maybe they clicked a different piece of theirs — re-select
        const piece = chess.get(square);
        if (piece && piece.color === chess.turn()) {
          setSelectedSq(square);
          const moves = chess.moves({ square, verbose: true });
          const highlights = {};
          highlights[square] = { background: "rgba(255,255,0,0.4)" };
          moves.forEach(m => {
            highlights[m.to] = {
              background: chess.get(m.to)
                ? "radial-gradient(circle, rgba(255,0,0,0.5) 70%, transparent 70%)"
                : "radial-gradient(circle, rgba(0,200,0,0.5) 30%, transparent 30%)",
              borderRadius: "50%",
            };
          });
          setOptionSquares(highlights);
        } else {
          setSelectedSq(null);
          setOptionSquares({});
        }
      }
      return;
    }

    // No square selected — select if it's the right color piece
    const piece = chess.get(square);
    if (!piece || piece.color !== chess.turn()) return;

    setSelectedSq(square);
    const moves = chess.moves({ square, verbose: true });
    const highlights = {};
    highlights[square] = { background: "rgba(255,255,0,0.4)" };
    moves.forEach(m => {
      highlights[m.to] = {
        background: chess.get(m.to)
          ? "radial-gradient(circle, rgba(255,0,0,0.5) 70%, transparent 70%)"
          : "radial-gradient(circle, rgba(0,200,0,0.5) 30%, transparent 30%)",
        borderRadius: "50%",
      };
    });
    setOptionSquares(highlights);
  }, [status, selectedSq, attemptMove]);

  // ── Reveal solution ────────────────────────────────────────────────────────
  const revealSolution = useCallback(() => {
    const chess = gameRef.current;
    if (!chess) return;
    setStatus("revealed");
    setSelectedSq(null);
    setOptionSquares({});
    setMessage("Solution revealed — study it, then try the next puzzle.");
    let delay = 0;
    for (let i = idxRef.current; i < solRef.current.length; i++) {
      const uci = solRef.current[i];
      delay += 700;
      setTimeout(() => {
        try {
          chess.move({ from: uci.slice(0,2), to: uci.slice(2,4), promotion: uci[4] || undefined });
          setFen(chess.fen());
        } catch {}
      }, delay);
    }
  }, []);

  const retry = useCallback(() => {
    setStatus("playing");
    setSelectedSq(null);
    setOptionSquares({});
    setMessage("Try again — click a piece to select it!");
  }, []);

  const badgeBg = { idle:"#1e3a5f", playing:"#1e3a5f", correct:"#166534", wrong:"#7f1d1d", revealed:"#374151" }[status] || "#1e3a5f";
  const badgeFg = { correct:"#4ade80", wrong:"#f87171" }[status] || "#cbd5e1";

  return (
    <div style={s.page}>
      <div style={s.topbar}>
        <button style={s.back} onClick={() => navigate("/")}>← Back</button>
        <div style={s.logo}>
          <img src="/bishop.png" alt="" style={s.logoImg} />
          <span>Viransh Chess — Puzzles</span>
        </div>
        {userRating !== null && (
          <div style={s.ratingBadge}>
            ⭐ {Math.round(userRating)}
            {ratingChange !== null && (
              <span style={{ color: ratingChange >= 0 ? "#4ade80" : "#f87171", marginLeft: 5 }}>
                {ratingChange >= 0 ? "+" : ""}{ratingChange}
              </span>
            )}
          </div>
        )}
      </div>

      <div style={s.body}>
        <div style={s.left}>
          {puzzleRating && (
            <div style={s.meta}>
              <span style={s.metaTheme}>{themes.split(" ").slice(0,3).join(" · ")}</span>
              <span style={s.metaRating}>Puzzle {puzzleRating}</span>
            </div>
          )}
          <div style={{ ...s.msgBar, background: badgeBg, color: badgeFg }}>
            {loading ? "Loading…" : message}
          </div>
          <div style={{ width: "580px" }}>
            <Chessboard options={{
              position: fen,
              onSquareClick: onSquareClick,
              allowDragging: false,
              squareStyles: optionSquares,
              boardStyle: { borderRadius: "6px", boxShadow: "0 4px 20px rgba(0,0,0,0.5)" },
            }} />
          </div>
          <p style={s.hint2}>Click a piece to select it, then click where to move</p>
        </div>

        <div style={s.right}>
          <div style={s.card}>
            <p style={s.label}>Your Progress</p>
            <div style={s.row}><span>Rating</span><strong>{userRating !== null ? Math.round(userRating) : "—"}</strong></div>
            <div style={s.row}><span>Solved</span><strong>{stats.solved}</strong></div>
            <div style={s.row}><span>Attempted</span><strong>{stats.attempted}</strong></div>
            {stats.attempted > 0 && (
              <div style={s.row}>
                <span>Accuracy</span>
                <strong>{Math.round((stats.solved / stats.attempted) * 100)}%</strong>
              </div>
            )}
          </div>

          <div style={s.btnCol}>
            {status === "wrong" && (
              <button style={{ ...s.btn, background: "#ca8a04" }} onClick={retry}>↺ Try Again</button>
            )}
            {(status === "correct" || status === "revealed") && (
              <button style={{ ...s.btn, background: "#16a34a" }} onClick={loadPuzzle} disabled={loading}>▶ Next Puzzle</button>
            )}
            {(status === "playing" || status === "wrong") && (
              <button style={{ ...s.btn, background: "#475569" }} onClick={revealSolution}>👁 Show Solution</button>
            )}
            <button style={{ ...s.btn, background: "#1e293b", border: "1px solid #334155" }} onClick={loadPuzzle} disabled={loading}>⏭ Skip</button>
          </div>

          <div style={s.hintBox}>
            <p style={s.label}>How it works</p>
            <p style={s.hintText}>Click a piece to select it (yellow highlight), then click the destination. Green dots show legal moves. Rating adjusts after each attempt.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

const s = {
  page:       { minHeight: "100vh", background: "#1a1a2e", color: "#fff" },
  topbar:     { display: "flex", alignItems: "center", gap: "1rem", padding: "12px 20px", borderBottom: "1px solid #1e3060", background: "#111827" },
  back:       { background: "transparent", border: "none", color: "#e2b96f", cursor: "pointer", fontSize: "1rem", padding: 0 },
  logo:       { display: "flex", alignItems: "center", gap: "8px", color: "#e2b96f", fontWeight: 700, fontSize: "1.1rem", flex: 1 },
  logoImg:    { width: "24px", height: "24px", objectFit: "contain" },
  ratingBadge:{ background: "#1e3a5f", padding: "5px 14px", borderRadius: "20px", fontSize: "0.9rem" },
  body:       { display: "flex", gap: "24px", padding: "20px", alignItems: "flex-start", flexWrap: "wrap" },
  left:       { display: "flex", flexDirection: "column", gap: "8px", flexShrink: 0 },
  meta:       { display: "flex", justifyContent: "space-between", width: "580px", fontSize: "0.75rem" },
  metaTheme:  { color: "#94a3b8", textTransform: "capitalize" },
  metaRating: { color: "#e2b96f" },
  msgBar:     { padding: "8px 12px", borderRadius: "6px", fontWeight: 600, textAlign: "center", fontSize: "0.88rem", width: "580px", boxSizing: "border-box" },
  hint2:      { margin: 0, textAlign: "center", fontSize: "0.72rem", color: "#475569" },
  right:      { flex: 1, minWidth: "200px", maxWidth: "260px", display: "flex", flexDirection: "column", gap: "14px" },
  card:       { background: "#16213e", borderRadius: "8px", padding: "14px", display: "flex", flexDirection: "column", gap: "8px" },
  label:      { margin: "0 0 2px", color: "#64748b", fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.08em" },
  row:        { display: "flex", justifyContent: "space-between", fontSize: "0.9rem", color: "#cbd5e1" },
  btnCol:     { display: "flex", flexDirection: "column", gap: "8px" },
  btn:        { padding: "10px", border: "none", borderRadius: "6px", fontWeight: 700, fontSize: "0.95rem", cursor: "pointer", color: "#fff" },
  hintBox:    { background: "#0f172a", borderRadius: "8px", padding: "12px" },
  hintText:   { margin: 0, color: "#64748b", fontSize: "0.82rem", lineHeight: 1.6 },
};
