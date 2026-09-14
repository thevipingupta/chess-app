#!/usr/bin/env bash
# Render.com build script for the chess-app backend
set -e

echo "==> Installing Stockfish chess engine..."
# Try apt first (Ubuntu/Debian); fall back to downloading the binary
if apt-get install -y stockfish 2>/dev/null; then
    echo "    Stockfish installed via apt -> /usr/games/stockfish"
else
    echo "    apt unavailable, downloading Stockfish binary..."
    mkdir -p "$HOME/stockfish"
    curl -fsSL "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-ubuntu-x86-64-avx2.tar" \
        | tar -x -C "$HOME/stockfish" --strip-components=1
    chmod +x "$HOME/stockfish/stockfish-ubuntu-x86-64-avx2"
    ln -sf "$HOME/stockfish/stockfish-ubuntu-x86-64-avx2" "$HOME/stockfish/stockfish"
    echo "    Stockfish installed at $HOME/stockfish/stockfish"
fi

echo "==> Installing Python dependencies..."
pip install uv
uv sync

echo "==> Build complete."
