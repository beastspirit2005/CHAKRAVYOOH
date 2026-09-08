import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.router import api_router
from src.config import get_settings
from src.database.session import init_db, async_session_maker
from src.cyclone.seed import seed_demo_zones
from src.cyclone.router import router as cyclone_router

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("pukar.backend")

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for the FastAPI application.
    Executes database schema creation on startup.
    """
    logger.info("Initializing Database...")
    await init_db()
    logger.info("Database initialized successfully.")
    
    logger.info("Seeding Chakravyooh Demo Zones...")
    async with async_session_maker() as db:
        await seed_demo_zones(db)
    
    # Check keys configured
    if settings.ENVIRONMENT.lower() != "production":
        logger.info("Running in DEVELOPMENT mode.")
    
    yield
    
    logger.info("Shutting down Pukar backend...")

app = FastAPI(
    title="Pukar Command Backend",
    description="Offline-first emergency communication mesh command node.",
    version="4.0.0",
    lifespan=lifespan
)

# CORS configuration for Web Command Center
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Gateway-ID", "X-App-Version"],
)

# Include core API routes
app.include_router(api_router, prefix="/api/v1")
app.include_router(cyclone_router, prefix="/api/v1")

@app.get("/health", tags=["Observability"])
async def health_check():
    """
    Ping endpoint used by Render / Load Balancers to prevent cold starts.
    """
    return {"status": "ok", "environment": settings.ENVIRONMENT}

@app.get("/ready", tags=["Observability"])
async def readiness_check():
    """
    Readiness probe for K8s / advanced deployments.
    """
    return {"status": "ready"}


# ── MOSDAC SCORPIO Live Satellite Feed Proxy ───────────────────────────────
# The God's Eye frontend embeds MOSDAC SCORPIO via iframes at /scorpio_feed.
# mosdac.gov.in sets X-Frame-Options: SAMEORIGIN which blocks direct embedding.
# This proxy fetches the upstream page server-side and strips those headers,
# so the iframe receives a clean HTML response it can render.
#
# Supported sub-paths: /scorpio_feed (root), /scorpio_feed/{path} (assets)
# ──────────────────────────────────────────────────────────────────────────

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

_MOSDAC_BASE = "https://mosdac.gov.in/scorpio"
_STRIP_HEADERS = {
    "x-frame-options", "content-security-policy",
    "x-content-type-options", "strict-transport-security",
}
_PROXY_TIMEOUT = 15.0


async def _proxy_mosdac(upstream_url: str, request: Request) -> Response:
    """Fetch upstream URL, strip embedding-hostile headers, return to browser."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_PROXY_TIMEOUT) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; PukarProxy/1.0)",
                "Accept": request.headers.get("accept", "*/*"),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://mosdac.gov.in/",
            }
            resp = await client.get(upstream_url, headers=headers)
            # Strip headers that would block iframe embedding
            clean_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in _STRIP_HEADERS
            }
            # Inject permissive framing header
            clean_headers["X-Frame-Options"] = "ALLOWALL"
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=clean_headers,
                media_type=resp.headers.get("content-type", "text/html"),
            )
    except httpx.TimeoutException:
        logger.warning("MOSDAC proxy timeout for %s", upstream_url)
        return Response(
            content="<html><body style='background:#0a0f1e;color:#64748b;font-family:monospace;padding:2rem'>"
                    "<h2>⚡ MOSDAC SCORPIO — Feed Temporarily Unavailable</h2>"
                    "<p>The ISRO MOSDAC server did not respond in time. Retrying…</p>"
                    "<script>setTimeout(()=>location.reload(),8000)</script></body></html>",
            status_code=504,
            media_type="text/html",
        )
    except Exception as e:
        logger.error("MOSDAC proxy error: %s", e)
        return Response(
            content="<html><body style='background:#0a0f1e;color:#ef4444;font-family:monospace;padding:2rem'>"
                    f"<h2>MOSDAC Proxy Error</h2><p>{str(e)[:200]}</p></body></html>",
            status_code=502,
            media_type="text/html",
        )


@app.get("/scorpio_feed", include_in_schema=False)
async def scorpio_feed_root(request: Request):
    """Proxy root MOSDAC SCORPIO page — embedded by God's Eye iframes."""
    return await _proxy_mosdac(_MOSDAC_BASE + "/", request)


@app.get("/scorpio_feed/{path:path}", include_in_schema=False)
async def scorpio_feed_asset(path: str, request: Request):
    """Proxy MOSDAC SCORPIO sub-paths (JS, CSS, tiles, images)."""
    return await _proxy_mosdac(f"{_MOSDAC_BASE}/{path}", request)


# ── Frontend Static File Serving ──────────────────────────────────────────────
# Arnav's HTML portal lives in <repo_root>/frontend/.
# FastAPI serves it on the same port 8000 — zero CORS, single unified server.
# Path: Pukar/frontend/index.html  → served at http://localhost:8000/
#        Pukar/frontend/static/…   → served at http://localhost:8000/static/…
_THIS_FILE   = Path(__file__).resolve()         # .../services/backend/src/main.py
_REPO_ROOT   = _THIS_FILE.parent.parent.parent.parent  # .../Pukar/
_FRONTEND_DIR = _REPO_ROOT / "frontend"


@app.get("/portal", include_in_schema=False)
@app.get("/", include_in_schema=False)
async def serve_portal():
    """Serve Arnav's Command Center UI."""
    html_file = _FRONTEND_DIR / "index.html"
    if html_file.exists():
        return FileResponse(str(html_file), media_type="text/html")
    return {"error": "Frontend not built. Run: git checkout upstream/Frontend-A -- index.html static/ && mkdir -p frontend && mv index.html frontend/ && mv static frontend/"}


# Mount /static AFTER defining API routes so /api/v1 is never shadowed.
# This must come last — StaticFiles is a catch-all.
_static_dir = _FRONTEND_DIR / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
    logger.info("Frontend static assets mounted from %s", _static_dir)
else:
    logger.warning(
        "Frontend directory not found at %s. "
        "Populate with: git checkout upstream/Frontend-A -- index.html static/ "
        "&& mkdir -p frontend && mv index.html frontend/ && mv static frontend/",
        _FRONTEND_DIR
    )
