# ── Chess App — Railway Dockerfile ───────────────────────────────────────────
# Single container: builds React frontend then serves everything via FastAPI

# ── Stage 1: build React frontend ────────────────────────────────────────────
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + built frontend ──────────────────────────────────
FROM python:3.11-slim

# Install Stockfish chess engine
RUN apt-get update && \
    apt-get install -y --no-install-recommends stockfish && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache \
        fastapi uvicorn[standard] python-chess stockfish \
        python-jose[cryptography] sqlalchemy python-dotenv \
        pydantic-settings python-multipart "pydantic[email]" \
        bcrypt psycopg2-binary aiofiles "httpx>=0.27.0"

# Copy backend source and seed scripts
COPY backend/ ./backend/
COPY seed_puzzles.py seed.py ./

# Copy built React frontend from stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8080
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
