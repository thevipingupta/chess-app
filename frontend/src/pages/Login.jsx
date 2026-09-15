import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { authApi } from "../api";

export default function Login() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState("");

  const handle = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    try {
      const { data } = await authApi.login(form.username, form.password);
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("username", data.username);
      navigate("/");
    } catch (err) {
      setError(err.response?.data?.detail || "Login failed");
    }
  };

  const playAsGuest = async () => {
    setError("");
    try {
      const { data } = await authApi.guest();
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("username", data.username);
      navigate("/");
    } catch {
      setError("Couldn't start a guest session. Try again.");
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.hero}>
        <img src="/bishop.png" alt="Bishop" style={styles.heroIcon} />
        <h1 style={styles.heroTitle}>Welcome To Viransh Chess App</h1>
        <p style={styles.heroSub}>Challenge the computer. Solve puzzles. Master the game.</p>
      </div>
      <div style={styles.card}>
        <h2 style={styles.title}>Sign In</h2>
        {error && <p style={styles.error}>{error}</p>}
        <form onSubmit={submit}>
          <input style={styles.input} name="username" placeholder="Username" value={form.username} onChange={handle} required />
          <input style={styles.input} name="password" type="password" placeholder="Password" value={form.password} onChange={handle} required />
          <button style={styles.btn} type="submit">Sign In</button>
        </form>
        <div style={styles.divider}><span style={styles.dividerText}>or</span></div>
        <button style={styles.guestBtn} type="button" onClick={playAsGuest}>
          Continue as Guest
        </button>
        <p style={styles.link}>No account? <Link to="/register">Register</Link></p>
      </div>
    </div>
  );
}

const styles = {
  page:      { minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "#1a1a2e", padding: "2rem", gap: "2rem" },
  hero:      { textAlign: "center" },
  heroIcon:  { width: "90px", height: "90px", marginBottom: "0.5rem", objectFit: "contain" },
  heroTitle: { fontSize: "2.6rem", fontWeight: 800, color: "#e2b96f", margin: "0 0 0.6rem", letterSpacing: "-0.5px" },
  heroSub:   { color: "#94a3b8", fontSize: "1.05rem", margin: 0 },
  card:      { background: "#16213e", padding: "2.5rem", borderRadius: "12px", width: "360px", boxShadow: "0 8px 32px rgba(0,0,0,0.4)" },
  title:     { textAlign: "center", color: "#fff", margin: "0 0 1.5rem", fontWeight: 400, fontSize: "1.2rem" },
  input: { display: "block", width: "100%", padding: "0.75rem", marginBottom: "1rem", borderRadius: "6px", border: "1px solid #334", background: "#0f3460", color: "#fff", fontSize: "1rem", boxSizing: "border-box" },
  btn: { width: "100%", padding: "0.8rem", background: "#e2b96f", color: "#1a1a2e", border: "none", borderRadius: "6px", fontSize: "1rem", fontWeight: 700, cursor: "pointer" },
  divider: { display: "flex", alignItems: "center", margin: "1.25rem 0", color: "#555", fontSize: "0.8rem" },
  dividerText: { margin: "0 auto" },
  guestBtn: { width: "100%", padding: "0.75rem", background: "transparent", color: "#e2b96f", border: "1px solid #e2b96f", borderRadius: "6px", fontSize: "0.95rem", fontWeight: 600, cursor: "pointer" },
  error: { color: "#ff6b6b", marginBottom: "1rem", textAlign: "center" },
  link: { textAlign: "center", color: "#aaa", marginTop: "1rem" },
};
