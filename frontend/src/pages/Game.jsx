import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import api from "../api";
import { playMoveSound, playOpponentMoveSound } from "../utils/sounds";

const DIFFICULTIES = [
  { level: 1,  label: "Beginner" },
  { level: 5,  label: "Casual" },
  { level: 10, label: "Intermediate" },
  { level: 15, label: "Advanced" },
  { level: 20, label: "Grandmaster" },
];

const STATUS_COLOR = {
  ok:        "#4ade80",
  check:     "#facc15",
  checkmate: "#f87171",
  stalemate: "#94a3b8",
  draw:      "#94a3b8",
};

const CLASS_META = {
  best:        { label: "Best",        color: "#4ade80", icon: "★" },
  excellent:   { label: "Excellent",   color: "#86efac", icon: "✓" },
  good:        { label: "Good",        color: "#93c5fd", icon: "+" },
  inaccuracy:  { label: "Inaccuracy",  color: "#fde047", icon: "?!" },
  mistake:     { label: "Mistake",     color: "#fb923c", icon: "?" },
  blunder:     { label: "Blunder",     color: "#f87171", icon: "??" },
};

export default function Game() {
  const navigate = useNavigate();
  const [difficulty, setDifficulty]   = useState(5);
  const [gameId, setGameId]           = useState(null);
  const [fen, setFen]                 = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  const [status, setStatus]           = useState("ok");
  const [gameOver, setGameOver]       = useState(false);
  const [winner, setWinner]           = useState(null);
  const [thinking, setThinking]       = useState(false);
  const [moveHistory, setMoveHistory] = useState([]);
  const [error, setError]             = useState("");

  // Click-to-move
  const [selectedSq, setSelectedSq]       = useState(null);
  const [optionSquares, setOptionSquares] = useState({});
  const [localChess, setLocalChess]       = useState(null);

  // Take back — allowed once per game
  const [takeBackUsed, setTakeBackUsed] = useState(false);

  // Analysis
  const [analysis, setAnalysis]         = useState(null);
  const [analyzing, setAnalyzing]       = useState(false);
  const [showAnalysis, setShowAnalysis] = useState(false);

  const resetToIdle = () => {
    setGameId(null);
    setFen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    setStatus("ok");
    setGameOver(false);
    setWinner(null);
    setMoveHistory([]);
    setLocalChess(null);
    setSelectedSq(null);
    setOptionSquares({});
    setAnalysis(null);
    setShowAnalysis(false);
    setTakeBackUsed(false);
    setError("");
  };

  const startGame = async () => {
    setError("");
    setSelectedSq(null);
    setOptionSquares({});
    setAnalysis(null);
    setShowAnalysis(false);
    setTakeBackUsed(false);
    try {
      const { data } = await api.post("/game/new", { difficulty });
      setGameId(data.game_id);
      setFen(data.fen);
      setStatus("ok");
      setGameOver(false);
      setWinner(null);
      setMoveHistory([]);
      setLocalChess(new Chess(data.fen));
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to start game");
    }
  };

  const sendMove = useCallback(async (from, to, piece) => {
    if (!gameId || gameOver || thinking) return false;
    let move = from + to;
    if (piece === "wP" && to[1] === "8") move += "q";
    if (piece === "bP" && to[1] === "1") move += "q";
    setThinking(true);
    setError("");
    setSelectedSq(null);
    setOptionSquares({});
    try {
      const { data } = await api.post(`/game/${gameId}/move`, { move });
      playMoveSound();
      if (data.computer_move) playOpponentMoveSound();
      setFen(data.fen);
      setStatus(data.status);
      setGameOver(data.game_over);
      setWinner(data.winner);
      setMoveHistory(h => [...h, { player: data.player_move, computer: data.computer_move }]);
      setTakeBackUsed(false);   // take back resets after each new move
      try { setLocalChess(new Chess(data.fen)); } catch {}
      return true;
    } catch (e) {
      setError(e.response?.data?.detail || "Move failed");
      return false;
    } finally {
      setThinking(false);
    }
  }, [gameId, gameOver, thinking]);

  // Click-to-move — react-chessboard v5 passes { square, piece } object
  const onSquareClick = useCallback(({ square }) => {
    if (!gameId || gameOver || thinking) return;

    if (selectedSq) {
      if (square === selectedSq) { setSelectedSq(null); setOptionSquares({}); return; }

      const piece = localChess?.get(selectedSq);
      const pieceCode = piece ? (piece.color === "w" ? "w" : "b") + piece.type.toUpperCase() : null;

      // Re-select if clicking another own white piece
      if (localChess) {
        const target = localChess.get(square);
        if (target && target.color === "w") {
          setSelectedSq(square);
          const moves = localChess.moves({ square, verbose: true });
          const hl = { [square]: { background: "rgba(255,255,0,0.4)" } };
          moves.forEach(m => { hl[m.to] = { background: localChess.get(m.to) ? "radial-gradient(circle, rgba(255,0,0,0.5) 70%, transparent 70%)" : "radial-gradient(circle, rgba(0,200,0,0.5) 30%, transparent 30%)", borderRadius: "50%" }; });
          setOptionSquares(hl);
          return;
        }
      }
      sendMove(selectedSq, square, pieceCode);
      return;
    }

    if (!localChess) return;
    const piece = localChess.get(square);
    if (!piece || piece.color !== "w") return;

    setSelectedSq(square);
    const moves = localChess.moves({ square, verbose: true });
    const hl = { [square]: { background: "rgba(255,255,0,0.4)" } };
    moves.forEach(m => { hl[m.to] = { background: localChess.get(m.to) ? "radial-gradient(circle, rgba(255,0,0,0.5) 70%, transparent 70%)" : "radial-gradient(circle, rgba(0,200,0,0.5) 30%, transparent 30%)", borderRadius: "50%" }; });
    setOptionSquares(hl);
  }, [gameId, gameOver, thinking, selectedSq, localChess, sendMove]);

  const takeBack = async () => {
    if (!gameId || thinking) return;
    setThinking(true);
    setError("");
    try {
      const { data } = await api.post(`/game/${gameId}/takeback`);
      setFen(data.fen);
      setStatus("ok");
      setGameOver(false);
      setWinner(null);
      setSelectedSq(null);
      setOptionSquares({});
      setMoveHistory(h => h.slice(0, -1));
      setTakeBackUsed(true);
      try { setLocalChess(new Chess(data.fen)); } catch {}
    } catch (e) {
      setError(e.response?.data?.detail || "Take back failed");
    } finally {
      setThinking(false);
    }
  };

  const runAnalysis = async () => {
    if (!gameId || analyzing) return;
    setAnalyzing(true);
    setShowAnalysis(true);
    setAnalysis(null);
    try {
      const { data } = await api.get(`/game/${gameId}/analyze`);
      setAnalysis(data);
    } catch (e) {
      setError(e.response?.data?.detail || "Analysis failed");
      setShowAnalysis(false);
    } finally {
      setAnalyzing(false);
    }
  };

  const statusText = () => {
    if (!gameId) return "Pick a difficulty and start a game";
    if (thinking) return "⏳ Computer is thinking…";
    if (status === "checkmate") return winner === "white" ? "🏆 You win!" : "💀 Computer wins";
    if (status === "stalemate") return "🤝 Stalemate";
    if (status === "draw")      return "🤝 Draw";
    if (status === "check")     return "⚠️ Check!";
    return "Your turn — click a piece then its destination";
  };

  return (
    <div style={s.page}>
      <div style={s.topbar}>
        <button style={s.back} onClick={() => navigate("/")}>← Back</button>
        <div style={s.logo}>
          <img src="/bishop.png" alt="" style={s.logoImg} />
          <span>Viransh Chess</span>
        </div>
      </div>

      <div style={s.outer}>
        {/* Left: board */}
        <div style={s.left}>
          <div style={{ ...s.statusBadge, background: STATUS_COLOR[status] || "#4ade80" }}>
            {statusText()}
          </div>
          {error && <div style={s.error}>{error}</div>}
          <div style={{ width: "580px" }}>
            <Chessboard options={{
              position: fen,
              onSquareClick: onSquareClick,
              allowDragging: false,
              squareStyles: optionSquares,
              boardStyle: { borderRadius: "6px", boxShadow: "0 4px 20px rgba(0,0,0,0.5)" },
            }} />
          </div>
          <p style={s.hint}>Click a piece to select, then click the destination</p>
        </div>

        {/* Right: controls */}
        <div style={s.right}>
          <div style={s.block}>
            <p style={s.label}>Difficulty</p>
            <div style={s.diffRow}>
              {DIFFICULTIES.map(d => (
                <button key={d.level}
                  style={{ ...s.diffBtn, ...(difficulty === d.level ? s.diffActive : {}) }}
                  onClick={() => setDifficulty(d.level)}
                  disabled={!!gameId && !gameOver}
                >{d.label}</button>
              ))}
            </div>
          </div>

          {/* No game yet or game over → start/play again */}
          {(!gameId || gameOver) && (
            <button style={s.startBtn} onClick={startGame} disabled={thinking}>
              {gameOver ? "▶ Play Again" : "▶ Start Game"}
            </button>
          )}
          {/* Game in progress → reset to idle so difficulty can be changed */}
          {gameId && !gameOver && (
            <button style={{ ...s.startBtn, background: "#374151" }}
              onClick={resetToIdle} disabled={thinking}>
              ↺ New Game
            </button>
          )}

          {gameId && !gameOver && difficulty <= 10 && moveHistory.length > 0 && !takeBackUsed && (
            <button style={{ ...s.startBtn, background: "#78350f", color: "#fde68a" }}
              onClick={takeBack} disabled={thinking}>
              ↩ Take Back
            </button>
          )}

          {gameOver && (
            <button style={{ ...s.startBtn, background: "#1e3a5f", color: "#93c5fd" }}
              onClick={runAnalysis} disabled={analyzing}>
              {analyzing ? "⏳ Analysing…" : "📊 Analyze Game"}
            </button>
          )}

          {moveHistory.length > 0 && !showAnalysis && (
            <div style={s.block}>
              <p style={s.label}>Move History</p>
              <div style={s.history}>
                {moveHistory.map((m, i) => (
                  <div key={i} style={s.row}>
                    <span style={s.num}>{i + 1}.</span>
                    <span style={s.white}>{m.player}</span>
                    <span style={s.black}>{m.computer || "—"}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Analysis panel ── */}
          {showAnalysis && (
            <div style={s.block}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <p style={s.label}>Game Analysis</p>
                <button style={s.closeBtn} onClick={() => setShowAnalysis(false)}>✕</button>
              </div>

              {analyzing && <p style={s.loading}>Running Stockfish analysis…</p>}

              {analysis && (
                <>
                  {/* Summary bar */}
                  <div style={s.summary}>
                    <div style={s.accuracyBig}>
                      <span style={s.accuracyNum}>{analysis.summary.accuracy}%</span>
                      <span style={s.accuracyLbl}>Accuracy</span>
                    </div>
                    <div style={s.summaryGrid}>
                      {Object.entries(CLASS_META).map(([key, meta]) => (
                        analysis.summary[key] > 0 && (
                          <div key={key} style={s.summaryItem}>
                            <span style={{ color: meta.color, fontWeight: 700 }}>{analysis.summary[key]}</span>
                            <span style={s.summaryKey}>{meta.label}</span>
                          </div>
                        )
                      ))}
                    </div>
                  </div>

                  {/* Move list */}
                  <div style={s.analysisList}>
                    {analysis.moves.map(m => {
                      const meta = CLASS_META[m.classification];
                      return (
                        <div key={m.move_num} style={s.analysisRow}>
                          <span style={s.moveNum}>{m.move_num}.</span>
                          <span style={{ ...s.moveUci, color: meta.color }}>{m.uci}</span>
                          <span style={{ ...s.classBadge, background: meta.color + "22", color: meta.color }}>
                            {meta.icon} {meta.label}
                          </span>
                          {!m.is_best && (
                            <span style={s.bestMove}>best: {m.best_move}</span>
                          )}
                          <span style={s.cpLoss}>−{m.cp_loss}cp</span>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const s = {
  page:        { minHeight: "100vh", background: "#1a1a2e", color: "#fff" },
  topbar:      { display: "flex", alignItems: "center", gap: "1rem", padding: "12px 20px", borderBottom: "1px solid #1e3060", background: "#111827" },
  back:        { background: "transparent", border: "none", color: "#e2b96f", cursor: "pointer", fontSize: "1rem", padding: 0 },
  logo:        { display: "flex", alignItems: "center", gap: "8px", color: "#e2b96f", fontWeight: 700, fontSize: "1.1rem" },
  logoImg:     { width: "26px", height: "26px", objectFit: "contain" },
  outer:       { display: "flex", gap: "24px", padding: "20px", alignItems: "flex-start", flexWrap: "wrap" },
  left:        { display: "flex", flexDirection: "column", gap: "10px", flexShrink: 0 },
  statusBadge: { padding: "8px 12px", borderRadius: "6px", fontWeight: 700, color: "#1a1a2e", textAlign: "center", fontSize: "0.95rem", width: "580px", boxSizing: "border-box" },
  error:       { background: "#7f1d1d", color: "#fca5a5", padding: "8px 12px", borderRadius: "6px", fontSize: "0.9rem", width: "580px", boxSizing: "border-box" },
  hint:        { margin: 0, textAlign: "center", fontSize: "0.72rem", color: "#475569" },
  right:       { flex: 1, minWidth: "220px", maxWidth: "320px", display: "flex", flexDirection: "column", gap: "14px" },
  block:       { display: "flex", flexDirection: "column", gap: "8px" },
  label:       { margin: 0, color: "#64748b", fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.08em" },
  diffRow:     { display: "flex", flexWrap: "wrap", gap: "6px" },
  diffBtn:     { padding: "6px 12px", borderRadius: "6px", border: "1px solid #2a3a5a", background: "#16213e", color: "#94a3b8", cursor: "pointer", fontSize: "0.85rem" },
  diffActive:  { background: "#e2b96f", color: "#1a1a2e", border: "1px solid #e2b96f", fontWeight: 700 },
  startBtn:    { padding: "10px", background: "#e2b96f", color: "#1a1a2e", border: "none", borderRadius: "6px", fontWeight: 700, fontSize: "1rem", cursor: "pointer" },
  history:     { background: "#0d1e3a", borderRadius: "6px", padding: "10px 12px", maxHeight: "200px", overflowY: "auto" },
  row:         { display: "flex", gap: "8px", padding: "3px 0", fontSize: "0.85rem", fontFamily: "monospace" },
  num:         { color: "#475569", minWidth: "22px" },
  white:       { color: "#e2b96f", minWidth: "55px" },
  black:       { color: "#94a3b8" },
  closeBtn:    { background: "transparent", border: "none", color: "#64748b", cursor: "pointer", fontSize: "1rem" },
  loading:     { color: "#64748b", fontSize: "0.85rem", textAlign: "center" },
  summary:     { background: "#0d1e3a", borderRadius: "8px", padding: "12px", display: "flex", gap: "12px", alignItems: "center" },
  accuracyBig: { display: "flex", flexDirection: "column", alignItems: "center", minWidth: "60px" },
  accuracyNum: { fontSize: "1.4rem", fontWeight: 700, color: "#4ade80" },
  accuracyLbl: { fontSize: "0.65rem", color: "#64748b", textTransform: "uppercase" },
  summaryGrid: { display: "flex", flexWrap: "wrap", gap: "6px 12px" },
  summaryItem: { display: "flex", flexDirection: "column", alignItems: "center", fontSize: "0.75rem" },
  summaryKey:  { color: "#64748b", fontSize: "0.65rem" },
  analysisList:{ background: "#0d1e3a", borderRadius: "6px", padding: "8px", maxHeight: "300px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "4px" },
  analysisRow: { display: "flex", alignItems: "center", gap: "6px", fontSize: "0.8rem", padding: "3px 0", borderBottom: "1px solid #1e2d4a" },
  moveNum:     { color: "#475569", minWidth: "20px", fontFamily: "monospace" },
  moveUci:     { fontWeight: 700, fontFamily: "monospace", minWidth: "36px" },
  classBadge:  { padding: "1px 6px", borderRadius: "4px", fontSize: "0.72rem", fontWeight: 600, whiteSpace: "nowrap" },
  bestMove:    { color: "#475569", fontSize: "0.7rem", fontFamily: "monospace", flex: 1 },
  cpLoss:      { color: "#475569", fontSize: "0.7rem", fontFamily: "monospace", marginLeft: "auto" },
};
