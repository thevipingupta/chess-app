import axios from "axios";

// Empty baseURL = relative URLs.
// - On Railway: frontend is served by FastAPI on the same origin → relative URLs work perfectly.
// - Locally: Vite proxy (vite.config.js) forwards /auth, /game, /puzzles, /analysis to localhost:8000.
const api = axios.create({ baseURL: "" });

// Attach JWT to every request if present
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// On 401 (expired / invalid token) — clear session and redirect to login
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("username");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export const authApi = {
  register: (data) => api.post("/auth/register", data),
  login: (username, password) => {
    const form = new URLSearchParams();
    form.append("username", username);
    form.append("password", password);
    return api.post("/auth/login", form);
  },
  guest: () => api.post("/auth/guest"),
};

export default api;
