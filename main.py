"""
main.py — Application entry point.

All route logic lives in the routes/ package.
This file only wires the app together.
"""
import os
import time
import pathlib
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse

from services.data_service import load_data, get_active_season, get_team_logo
from routes.standings_routes import router as standings_router, templates as standings_templates
from routes.history_routes import router as history_router, templates as history_templates
from routes.draft_routes import router as draft_router, templates as draft_templates
from routes.api_routes import router as api_router
from routes.auth_routes import router as auth_router
from routes.admin_routes import router as admin_router, _page_router as admin_page_router
from routes.prediction_routes import router as prediction_router

app = FastAPI(title="WinsPool")

# ── CORS ──────────────────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

# Read allowed origins from env (comma-separated). Never use wildcard *.
# Dev default: localhost only. Production: set CORS_ORIGINS in Cloud Run env vars.
_cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:8000")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Middleware ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    if os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true":
        start_time = time.time()
        print(f"[DEBUG_PAGE_LOAD] Started {request.method} {request.url.path}")
        response = await call_next(request)
        process_time = time.time() - start_time
        print(f"[DEBUG_PAGE_LOAD] Completed {request.method} {request.url.path} in {process_time:.4f} secs")
        return response
    return await call_next(request)

# Register Jinja2 globals
for t in [standings_templates, history_templates, draft_templates]:
    t.env.globals['get_team_logo'] = get_team_logo

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_PATH = os.environ.get("STATIC_PATH", "static")
if not pathlib.Path(STATIC_PATH).exists():
    pathlib.Path(STATIC_PATH).mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")


@app.get("/sw.js", include_in_schema=False)
async def serve_service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(standings_router)
app.include_router(history_router)
app.include_router(draft_router)
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(admin_page_router)
app.include_router(prediction_router)

# ── Root redirect ─────────────────────────────────────────────────────────────
@app.get("/")
async def root_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    active = get_active_season(games, draft_results)
    return RedirectResponse(f"/wins-pool/{active}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
