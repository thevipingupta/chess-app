import { useState, useRef, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Chessboard } from "react-chessboard";
import { Chess } from "chess.js";
import api from "../api";

// Build a FEN snapshot array from the move list returned by the API.
// fens[0] = starting position, fens[n] = position after move index n-1.
// Uses SAN notation (m.san) — most reliable with chess.js v1.x.
function buildFens(moves) {
  const chess = new Chess();
  const fens  = [chess.fen()];
  for (const m of moves) {
    try {
      chess.move(m.san);          // SAN: "e4", "Nf3", "O-O", etc.
    } catch {
      // SAN failed — try UCI object form as fallback
      try {
        const from = m.uci.slice(0, 2);
        const to   = m.uci.slice(2, 4);
        const promo = m.uci[4];
        chess.move(promo ? { from, to, promotion: promo } : { from, to });
      } catch {
        // move totally failed — repeat last position so indexing stays aligned
      }
    }
    fens.push(chess.fen());
  }
  return fens;
}

// ── Classification meta ──────────────────────────────────────────────────────
const CLASS_META = {
  best:       { label: "Best",       color: "#4ade80", icon: "★", bg: "#14532d33" },
  excellent:  { label: "Excellent",  color: "#86efac", icon: "✓", bg: "#16652e22" },
  good:       { label: "Good",       color: "#60a5fa", icon: "+", bg: "#1e3a5f33" },
  inaccuracy: { label: "Inaccuracy", color: "#fde047", icon: "?!", bg: "#713f1233" },
  mistake:    { label: "Mistake",    color: "#fb923c", icon: "?", bg: "#7c2d1233" },
  blunder:    { label: "Blunder",    color: "#f87171", icon: "??", bg: "#7f1d1d33" },
};

const STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

// ── Eval bar ─────────────────────────────────────────────────────────────────
function EvalBar({ evalVal }) {
  // evalVal is centipawns from White's POV (positive = white better)
  // or a string like "M3" / "M-2"
  let pct = 50; // percentage for white (top of bar)
  let label = "=";

  if (typeof evalVal === "number") {
    const clamped = Math.max(-800, Math.min(800, evalVal));
    pct = 50 - (clamped / 800) * 46; // 4–96%
    label = evalVal > 0 ? `+${(evalVal / 100).toFixed(1)}` : (evalVal / 100).toFixed(1);
  } else if (typeof evalVal === "string" && evalVal.startsWith("M")) {
    const n = parseInt(evalVal.slice(1));
    pct  = n > 0 ? 4 : 96;
    label = evalVal;
  }

  return (
    <div style={s.evalBarWrap} title={`Eval: ${label}`}>
      <div style={{ ...s.evalBarBlack, height: `${pct}%` }} />
      <div style={{ ...s.evalBarWhite, height: `${100 - pct}%` }} />
      <div style={{ ...s.evalLabel, top: pct < 50 ? "4px" : undefined, bottom: pct >= 50 ? "4px" : undefined }}>
        {label}
      </div>
    </div>
  );
}

// ── Arrow highlighting: from/to squares of current move ──────────────────────
function squareStyles(move) {
  if (!move) return {};
  const uci = move.uci;
  if (!uci || uci.length < 4) return {};
  return {
    [uci.slice(0, 2)]: { background: "rgba(226,185,111,0.55)", borderRadius: "4px" },
    [uci.slice(2, 4)]: { background: "rgba(226,185,111,0.85)", borderRadius: "4px" },
  };
}

// ── Voice: convert SAN to natural speech ────────────────────────────────────
function speakableSan(san) {
  if (san === "O-O")   return "castles kingside";
  if (san === "O-O-O") return "castles queenside";
  return san
    .replace(/^N/, "Knight ").replace(/^B/, "Bishop ").replace(/^R/, "Rook ")
    .replace(/^Q/, "Queen ") .replace(/^K/, "King ")
    .replace(/x/, " takes ")
    .replace(/\+/, ", check").replace(/#/, ", checkmate")
    .replace(/=Q/, ", promotes to Queen").replace(/=R/, ", promotes to Rook")
    .replace(/=B/, ", promotes to Bishop").replace(/=N/, ", promotes to Knight");
}

export default function Analyze() {
  const navigate  = useNavigate();
  const fileRef   = useRef(null);

  const [pgnText,      setPgnText]      = useState("");
  const [loading,      setLoading]      = useState(false);
  const [ocrLoading,   setOcrLoading]   = useState(false);
  const [ocrNote,      setOcrNote]      = useState("");   // "OCR result — please verify"
  const [error,        setError]        = useState("");
  const [result,       setResult]       = useState(null);
  const [cursor,       setCursor]       = useState(-1);
  const [boardFen,     setBoardFen]     = useState(STARTING_FEN);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const fensRef    = useRef([STARTING_FEN]);  // ref — always current, no async lag
  const voiceRef   = useRef(true);            // mirrors voiceEnabled, readable in callbacks
  const ocrRef     = useRef(null);            // hidden file input for OCR uploads

  // ── Speak a phrase via Web Speech API ───────────────────────────────────────
  const speak = useCallback((text) => {
    if (!voiceRef.current || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();                   // stop any ongoing utterance
    const u = new SpeechSynthesisUtterance(text);
    u.rate  = 1.1;
    u.pitch = 1;
    window.speechSynthesis.speak(u);
  }, []);

  const toggleVoice = () => {
    setVoiceEnabled(v => { voiceRef.current = !v; return !v; });
    window.speechSynthesis?.cancel();
  };

  // Build FEN array into ref the moment result arrives, reset board to start
  useEffect(() => {
    if (!result) {
      fensRef.current = [STARTING_FEN];
      setBoardFen(STARTING_FEN);
      return;
    }
    fensRef.current = buildFens(result.moves);
    setCursor(-1);
    setBoardFen(STARTING_FEN);
  }, [result]);

  // Single navigation function — updates cursor + boardFen atomically + speaks
  const stepTo = useCallback((next) => {
    const total = result?.moves?.length ?? 0;
    const clamped = Math.max(-1, Math.min(total - 1, next));
    setCursor(clamped);
    setBoardFen(fensRef.current[clamped + 1] ?? STARTING_FEN);
    // Voice
    if (clamped === -1) {
      speak("Starting position");
    } else {
      const move = result.moves[clamped];
      const side = move.side === "white" ? "White" : "Black";
      speak(`${side} ${speakableSan(move.san)}`);
    }
  }, [result, speak]);

  // Keyboard navigation
  useEffect(() => {
    if (!result) return;
    const handler = (e) => {
      if (e.key === "ArrowLeft")  stepTo(cursor - 1);
      if (e.key === "ArrowRight") stepTo(cursor + 1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [result, cursor, stepTo]);

  // ── File upload (.pgn / .txt) ────────────────────────────────────────────────
  const onFileChange = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => { setPgnText(ev.target.result || ""); setOcrNote(""); };
    reader.readAsText(file);
    e.target.value = "";
  }, []);

  // ── OCR upload (image / PDF) ─────────────────────────────────────────────────
  const onOcrFileChange = useCallback(async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    setOcrLoading(true);
    setError("");
    setOcrNote("");

    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.post("/analysis/extract-pgn", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setPgnText(res.data.pgn || "");
      setOcrNote("✨ OCR result — please review and correct before analysing");
    } catch (err) {
      setError(err.response?.data?.detail || "OCR failed. Try a clearer image.");
    } finally {
      setOcrLoading(false);
    }
  }, []);

  // ── Submit ──────────────────────────────────────────────────────────────────
  const analyze = async () => {
    if (!pgnText.trim()) { setError("Paste a PGN or upload a .pgn file first."); return; }
    setError("");
    setLoading(true);
    setResult(null);
    fensRef.current = [STARTING_FEN];
    setCursor(-1);
    setBoardFen(STARTING_FEN);
    try {
      const res = await api.post("/analysis/pgn", { pgn: pgnText });
      setResult(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || "Analysis failed. Check the PGN and try again.");
    } finally {
      setLoading(false);
    }
  };

  // ── Derived display values ───────────────────────────────────────────────────
  const currentMove = result?.moves?.[cursor] ?? null;
  const evalNow  = currentMove?.eval_after ?? 0;

  // Summary counts
  const summary = result ? result.moves.reduce((acc, m) => {
    acc[m.classification] = (acc[m.classification] || 0) + 1;
    return acc;
  }, {}) : {};

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <button style={s.backBtn} onClick={() => navigate("/")}>← Home</button>
        <h1 style={s.title}>📋 PGN Analyzer</h1>
        <span style={s.subtitle}>Upload a game, step through every move with Stockfish insight</span>
      </div>

      {!result ? (
        /* ── Input panel ── */
        <div style={s.inputCard}>
          <div style={s.inputRow}>
            <textarea
              style={s.textarea}
              placeholder={`Paste your PGN here…\n\nExample:\n[Event "Casual Game"]\n[White "You"]\n[Black "Opponent"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 ...`}
              value={pgnText}
              onChange={e => { setPgnText(e.target.value); setOcrNote(""); }}
              spellCheck={false}
            />
          </div>
          {ocrNote && (
            <div style={s.ocrNote}>{ocrNote}</div>
          )}
          <div style={s.btnRow}>
            {/* PGN file upload */}
            <button style={s.uploadBtn} onClick={() => fileRef.current?.click()}>
              📂 Upload .pgn file
            </button>
            <input ref={fileRef} type="file" accept=".pgn,.txt" hidden onChange={onFileChange} />

            {/* OCR upload */}
            <button
              style={{ ...s.uploadBtn, background: "#1e3a5f", borderColor: "#60a5fa" }}
              onClick={() => ocrRef.current?.click()}
              disabled={ocrLoading}
              title="Upload a scanned scoresheet, photo, or PDF — AI will read the notation"
            >
              {ocrLoading ? "⏳ Reading…" : "🔎 Scan image / PDF"}
            </button>
            <input
              ref={ocrRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,application/pdf"
              hidden
              onChange={onOcrFileChange}
            />

            <button style={s.analyzeBtn} onClick={analyze} disabled={loading || ocrLoading}>
              {loading ? "⏳ Analysing…" : "🔍 Analyse Game"}
            </button>
          </div>
          {ocrLoading && (
            <div style={s.progress}>
              <div style={s.spinner} />
              <span>Claude Vision is reading the notation… this takes a few seconds.</span>
            </div>
          )}
          {loading && (
            <div style={s.progress}>
              <div style={s.spinner} />
              <span>Stockfish is evaluating every move… this takes ~10–30 seconds.</span>
            </div>
          )}
          {error && <div style={s.errorBox}>{error}</div>}
        </div>
      ) : (
        /* ── Analysis view ── */
        <div style={s.analyzeLayout}>

          {/* Left: eval bar + board + nav */}
          <div style={s.boardCol}>
            {/* Game info strip */}
            {(result.headers.White || result.headers.Black) && (
              <div style={s.gameInfo}>
                <span style={s.playerName}>♙ {result.headers.White || "White"}</span>
                <span style={s.vs}>vs</span>
                <span style={s.playerName}>♟ {result.headers.Black || "Black"}</span>
                {result.headers.Result && <span style={s.gameResult}>{result.headers.Result}</span>}
                {result.headers.Date && <span style={s.gameDate}>{result.headers.Date}</span>}
              </div>
            )}

            <div style={s.boardAndEval}>
              <EvalBar evalVal={evalNow} />
              <div style={{ width: "500px" }}>
                <Chessboard
                  key={boardFen}
                  options={{
                    position: boardFen,
                    arePiecesDraggable: false,
                    customSquareStyles: squareStyles(currentMove),
                  }}
                />
              </div>
            </div>

            {/* Move classification badge */}
            {currentMove && (
              <div style={{ ...s.classBadge, background: CLASS_META[currentMove.classification]?.bg, borderColor: CLASS_META[currentMove.classification]?.color }}>
                <span style={{ color: CLASS_META[currentMove.classification]?.color, fontWeight: 700, fontSize: "1.1rem" }}>
                  {CLASS_META[currentMove.classification]?.icon} {CLASS_META[currentMove.classification]?.label}
                </span>
                <span style={s.classDetail}>
                  {currentMove.side === "white" ? "White" : "Black"} played <strong>{currentMove.san}</strong>
                  {!currentMove.is_best && <> · Best was <strong>{currentMove.best_move}</strong></>}
                  {currentMove.cp_loss > 0 && <> · −{currentMove.cp_loss} cp</>}
                </span>
              </div>
            )}
            {cursor === -1 && <div style={s.startHint}>Press → or click a move to begin</div>}

            {/* Navigation */}
            <div style={s.navRow}>
              <button style={s.navBtn} onClick={() => stepTo(-1)} title="Start">⏮</button>
              <button style={s.navBtn} onClick={() => stepTo(cursor - 1)} title="Previous (←)">◀</button>
              <span style={s.moveCounter}>
                {cursor === -1 ? "Start" : `Move ${currentMove?.move_num} (${currentMove?.side === "white" ? "W" : "B"})`}
              </span>
              <button style={s.navBtn} onClick={() => stepTo(cursor + 1)} title="Next (→)">▶</button>
              <button style={s.navBtn} onClick={() => stepTo(result.moves.length - 1)} title="End">⏭</button>
              <button
                style={{ ...s.navBtn, opacity: voiceEnabled ? 1 : 0.4, fontSize: "1.1rem" }}
                onClick={toggleVoice}
                title={voiceEnabled ? "Voice on — click to mute" : "Voice off — click to unmute"}
              >
                {voiceEnabled ? "🔊" : "🔇"}
              </button>
            </div>

            {/* New analysis button */}
            <button style={s.newBtn} onClick={() => { setResult(null); setPgnText(""); }}>
              ↺ Analyse another game
            </button>
          </div>

          {/* Right: move list + summary */}
          <div style={s.rightCol}>
            {/* Summary chips */}
            <div style={s.summaryRow}>
              {Object.entries(CLASS_META).map(([key, meta]) =>
                summary[key] ? (
                  <div key={key} style={{ ...s.summaryChip, background: meta.bg, borderColor: meta.color }}>
                    <span style={{ color: meta.color }}>{meta.icon}</span>
                    <span style={{ color: meta.color, fontWeight: 600 }}>{summary[key]}</span>
                    <span style={s.chipLabel}>{meta.label}</span>
                  </div>
                ) : null
              )}
            </div>

            {/* Move list */}
            <div style={s.moveList}>
              <div style={s.moveListHeader}>Move List <span style={s.moveListHint}>click to jump · ← → to step</span></div>
              <div style={s.moveListScroll}>
                {/* Pair moves: white + black on same row */}
                {Array.from({ length: Math.ceil(result.moves.length / 2) }, (_, pairIdx) => {
                  const wIdx = pairIdx * 2;
                  const bIdx = pairIdx * 2 + 1;
                  const wMove = result.moves[wIdx];
                  const bMove = result.moves[bIdx];
                  return (
                    <div key={pairIdx} style={s.movePair}>
                      <span style={s.moveNum}>{wMove.move_num}.</span>
                      <MoveChip move={wMove} idx={wIdx} cursor={cursor} onStep={stepTo} />
                      {bMove && <MoveChip move={bMove} idx={bIdx} cursor={cursor} onStep={stepTo} />}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MoveChip({ move, idx, cursor, onStep }) {
  const meta   = CLASS_META[move.classification] || CLASS_META.good;
  const active = cursor === idx;
  return (
    <button
      style={{
        ...s.moveChip,
        background:   active ? meta.color + "33" : "transparent",
        borderColor:  active ? meta.color : "transparent",
        color:        active ? "#fff" : "#ccc",
      }}
      onClick={() => onStep(idx)}
      title={`${meta.label}${move.cp_loss > 0 ? ` · −${move.cp_loss}cp` : ""}`}
    >
      <span style={{ color: meta.color, fontSize: "0.7rem" }}>{meta.icon}</span>
      {move.san}
    </button>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────
const s = {
  page:          { minHeight: "100vh", background: "#1a1a2e", color: "#fff", padding: "1.5rem", fontFamily: "system-ui, sans-serif" },
  header:        { display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1.5rem", flexWrap: "wrap" },
  backBtn:       { background: "transparent", border: "1px solid #555", color: "#aaa", padding: "0.4rem 0.9rem", borderRadius: "6px", cursor: "pointer", fontSize: "0.85rem" },
  title:         { color: "#e2b96f", margin: 0, fontSize: "1.6rem" },
  subtitle:      { color: "#666", fontSize: "0.85rem" },

  inputCard:     { maxWidth: "700px", margin: "0 auto", background: "#16213e", borderRadius: "16px", padding: "2rem", boxShadow: "0 4px 24px rgba(0,0,0,0.4)" },
  inputRow:      { marginBottom: "1rem" },
  textarea:      { width: "100%", minHeight: "220px", background: "#0f172a", color: "#e2e8f0", border: "1px solid #334", borderRadius: "8px", padding: "0.9rem", fontSize: "0.82rem", fontFamily: "monospace", resize: "vertical", boxSizing: "border-box" },
  btnRow:        { display: "flex", gap: "0.75rem", flexWrap: "wrap" },
  uploadBtn:     { background: "#334155", color: "#e2e8f0", border: "none", padding: "0.7rem 1.2rem", borderRadius: "8px", cursor: "pointer", fontWeight: 600 },
  analyzeBtn:    { background: "#e2b96f", color: "#1a1a2e", border: "none", padding: "0.7rem 1.6rem", borderRadius: "8px", cursor: "pointer", fontWeight: 700, fontSize: "1rem" },
  progress:      { display: "flex", alignItems: "center", gap: "0.75rem", marginTop: "1rem", color: "#94a3b8", fontSize: "0.85rem" },
  spinner:       { width: "18px", height: "18px", border: "3px solid #334", borderTop: "3px solid #e2b96f", borderRadius: "50%", animation: "spin 0.8s linear infinite" },
  errorBox:      { marginTop: "1rem", background: "#7f1d1d44", border: "1px solid #f87171", borderRadius: "8px", padding: "0.75rem 1rem", color: "#fca5a5", fontSize: "0.9rem" },
  ocrNote:       { marginBottom: "0.75rem", background: "#1e3a5f55", border: "1px solid #60a5fa", borderRadius: "8px", padding: "0.6rem 1rem", color: "#93c5fd", fontSize: "0.85rem" },

  analyzeLayout: { display: "flex", gap: "1.5rem", alignItems: "flex-start", flexWrap: "wrap" },
  boardCol:      { display: "flex", flexDirection: "column", gap: "0.75rem", alignItems: "flex-start" },
  gameInfo:      { display: "flex", alignItems: "center", gap: "0.75rem", background: "#16213e", borderRadius: "8px", padding: "0.5rem 0.9rem", fontSize: "0.85rem" },
  playerName:    { color: "#e2b96f", fontWeight: 600 },
  vs:            { color: "#555" },
  gameResult:    { color: "#4ade80", fontWeight: 700, marginLeft: "0.5rem" },
  gameDate:      { color: "#555", fontSize: "0.78rem" },

  boardAndEval:  { display: "flex", alignItems: "flex-end", gap: "0.5rem" },
  evalBarWrap:   { width: "20px", height: "500px", borderRadius: "6px", overflow: "hidden", position: "relative", background: "#111" },
  evalBarBlack:  { background: "#222", width: "100%", transition: "height 0.4s ease" },
  evalBarWhite:  { background: "#e2e8f0", width: "100%", transition: "height 0.4s ease" },
  evalLabel:     { position: "absolute", left: 0, right: 0, textAlign: "center", fontSize: "0.55rem", color: "#888", fontWeight: 700, padding: "1px" },

  classBadge:    { display: "flex", flexDirection: "column", gap: "0.2rem", padding: "0.6rem 1rem", borderRadius: "8px", border: "1px solid", width: "500px", boxSizing: "border-box" },
  classDetail:   { color: "#aaa", fontSize: "0.83rem" },
  startHint:     { color: "#555", fontSize: "0.85rem", textAlign: "center", width: "500px" },
  navRow:        { display: "flex", alignItems: "center", gap: "0.5rem", width: "500px", justifyContent: "center" },
  navBtn:        { background: "#16213e", border: "1px solid #334", color: "#e2b96f", padding: "0.4rem 0.9rem", borderRadius: "6px", cursor: "pointer", fontSize: "1rem" },
  moveCounter:   { color: "#aaa", fontSize: "0.85rem", minWidth: "120px", textAlign: "center" },
  newBtn:        { background: "transparent", border: "1px solid #555", color: "#aaa", padding: "0.4rem 1rem", borderRadius: "6px", cursor: "pointer", fontSize: "0.85rem", marginTop: "0.25rem" },

  rightCol:      { flex: 1, minWidth: "280px", maxWidth: "380px", display: "flex", flexDirection: "column", gap: "0.75rem" },
  summaryRow:    { display: "flex", flexWrap: "wrap", gap: "0.4rem" },
  summaryChip:   { display: "flex", alignItems: "center", gap: "0.3rem", padding: "0.25rem 0.6rem", borderRadius: "20px", border: "1px solid", fontSize: "0.78rem" },
  chipLabel:     { color: "#888" },

  moveList:      { background: "#16213e", borderRadius: "12px", overflow: "hidden" },
  moveListHeader:{ padding: "0.6rem 0.9rem", background: "#0f172a", color: "#e2b96f", fontWeight: 600, fontSize: "0.85rem", display: "flex", justifyContent: "space-between", alignItems: "center" },
  moveListHint:  { color: "#444", fontWeight: 400, fontSize: "0.72rem" },
  moveListScroll:{ maxHeight: "520px", overflowY: "auto", padding: "0.4rem" },
  movePair:      { display: "flex", alignItems: "center", gap: "0.2rem", marginBottom: "0.15rem" },
  moveNum:       { color: "#555", fontSize: "0.75rem", minWidth: "28px", textAlign: "right" },
  moveChip:      { padding: "0.25rem 0.5rem", borderRadius: "5px", border: "1px solid", cursor: "pointer", fontSize: "0.82rem", fontWeight: 500, display: "flex", alignItems: "center", gap: "0.3rem", minWidth: "64px" },
};

// Inject keyframe for spinner
const styleTag = document.createElement("style");
styleTag.textContent = `@keyframes spin { to { transform: rotate(360deg); } }`;
document.head.appendChild(styleTag);
