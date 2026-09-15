"""Chess App — FastAPI entry point.

Start with:
    uvicorn backend.main:app --reload --port 8000

Swagger UI: http://localhost:8000/docs
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import Base, engine
from backend.auth.router import router as auth_router
from backend.game.router import router as game_router
from backend.puzzles.router import router as puzzles_router
from backend.analysis.router import router as analysis_router

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Chess App", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(game_router)
app.include_router(puzzles_router)
app.include_router(analysis_router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Serve built React frontend (production / Railway) ─────────────────────────
# Only active when `frontend/dist` exists (i.e. after `npm run build`).
# Locally you run `npm run dev` separately, so this block is skipped.
_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def _serve_spa(full_path: str):
        """Serve a real file if it exists (e.g. bishop.png), otherwise hand off to React Router."""
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_DIST / "index.html"))
