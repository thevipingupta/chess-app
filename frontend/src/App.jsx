import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Home from "./pages/Home";
import Game from "./pages/Game";
import Puzzles from "./pages/Puzzles";

function RequireAuth({ children }) {
  return localStorage.getItem("token") ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
        <Route path="/game" element={<RequireAuth><Game /></RequireAuth>} />
        <Route path="/puzzles" element={<RequireAuth><Puzzles /></RequireAuth>} />
      </Routes>
    </BrowserRouter>
  );
}
