"""
main.py — Application entry point.

All route logic lives in the routes/ package.
This file only wires the app together.
"""
import os
import pathlib
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from services.data_service import load_data, get_active_season
from routes.standings_routes import router as standings_router
from routes.history_routes import router as history_router
from routes.draft_routes import router as draft_router
from routes.api_routes import router as api_router

app = FastAPI(title="WinsPool")

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_PATH = os.environ.get("STATIC_PATH", "static")
if not pathlib.Path(STATIC_PATH).exists():
    pathlib.Path(STATIC_PATH).mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(standings_router)
app.include_router(history_router)
app.include_router(draft_router)
app.include_router(api_router)

# ── Root redirect ─────────────────────────────────────────────────────────────
@app.get("/")
async def root_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    active = get_active_season(games, draft_results)
    return RedirectResponse(f"/wins-pool/{active}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
