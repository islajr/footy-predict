from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.main import init_app
from src.api.routes import analysis, picks, fetch, accuracy, settings, views, parlay

logger = logging.getLogger("footy.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: boots database and sets it on app state."""
    logger.info("Initializing Footy Predict database...")
    if not hasattr(app.state, "db") or app.state.db is None:
        db = init_app()
        app.state.db = db
    yield
    logger.info("Footy Predict database shutting down.")


app = FastAPI(
    title="Footy Predict API",
    description="Local API layer for the Footy Predict parlay construction engine",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static and Templates setup
PROJECT_ROOT = Path(__file__).parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "src" / "static"
TEMPLATES_DIR = PROJECT_ROOT / "src" / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", tags=["System"])
def health_check(request: Request):
    """Retrieve system health and database connection status."""
    db_connected = False
    try:
        db = request.app.state.db
        with db._connect() as conn:
            conn.execute("SELECT 1")
            db_connected = True
    except Exception:
        pass

    return {
        "status": "ok",
        "database_connected": db_connected,
    }


# Include Routers under v1 API
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(picks.router, prefix="/api/v1")
app.include_router(fetch.router, prefix="/api/v1")
app.include_router(accuracy.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(parlay.router, prefix="/api/v1")


app.include_router(views.router)


def main():
    """Main CLI entrypoint for running the web application server."""
    import uvicorn
    uvicorn.run("src.api.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
