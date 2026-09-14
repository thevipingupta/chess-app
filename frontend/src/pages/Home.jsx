import { useNavigate } from "react-router-dom";

export default function Home() {
  const username = localStorage.getItem("username") || "Player";
  const navigate = useNavigate();

  const logout = () => {
    localStorage.clear();
    navigate("/login");
  };

  return (
    <div style={styles.page}>
      <div style={styles.header}>
        <h1 style={styles.logo}><img src="/bishop.png" alt="Bishop" style={{ width: "36px", height: "36px", objectFit: "contain", verticalAlign: "middle", marginRight: "0.5rem" }} />Viransh Chess App</h1>
        <div style={styles.userRow}>
          <span style={styles.username}>👤 {username}</span>
          <button style={styles.logoutBtn} onClick={logout}>Logout</button>
        </div>
      </div>

      <div style={styles.cards}>
        <div style={styles.card} onClick={() => navigate("/game")}>
          <div style={styles.cardIcon}><img src="/bishop.png" alt="Bishop" style={{ width: "64px", height: "64px", objectFit: "contain" }} /></div>
          <h2 style={styles.cardTitle}>Play vs Computer</h2>
          <p style={styles.cardDesc}>Challenge Stockfish at any difficulty level — from beginner to grandmaster.</p>
          <button style={styles.btn}>Play Now →</button>
        </div>

        <div style={styles.card} onClick={() => navigate("/puzzles")}>
          <div style={styles.cardIcon}>🧩</div>
          <h2 style={styles.cardTitle}>Chess Puzzles</h2>
          <p style={styles.cardDesc}>Solve tactical puzzles that get harder as your rating climbs.</p>
          <button style={styles.btn}>Solve Puzzles →</button>
        </div>
      </div>
    </div>
  );
}

const styles = {
  page: { minHeight: "100vh", background: "#1a1a2e", color: "#fff", padding: "2rem" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "3rem" },
  logo: { color: "#e2b96f", margin: 0, fontSize: "1.8rem" },
  userRow: { display: "flex", alignItems: "center", gap: "1rem" },
  username: { color: "#aaa" },
  logoutBtn: { background: "transparent", border: "1px solid #555", color: "#aaa", padding: "0.4rem 1rem", borderRadius: "6px", cursor: "pointer" },
  cards: { display: "flex", gap: "2rem", justifyContent: "center", marginTop: "4rem", flexWrap: "wrap" },
  card: { background: "#16213e", borderRadius: "16px", padding: "2.5rem", width: "300px", textAlign: "center", cursor: "pointer", transition: "transform 0.2s", boxShadow: "0 4px 20px rgba(0,0,0,0.3)" },
  cardIcon: { fontSize: "4rem", marginBottom: "1rem" },
  cardTitle: { color: "#e2b96f", marginBottom: "0.75rem" },
  cardDesc: { color: "#aaa", lineHeight: 1.6, marginBottom: "1.5rem" },
  btn: { background: "#e2b96f", color: "#1a1a2e", border: "none", padding: "0.7rem 1.5rem", borderRadius: "8px", fontWeight: 700, cursor: "pointer", fontSize: "1rem" },
};
