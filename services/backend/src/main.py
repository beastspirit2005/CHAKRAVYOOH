import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure services/backend is in sys.path when deployed on Vercel
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
_SRC_DIR = _BACKEND_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

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
    DB init and seeding are best-effort — failures are logged but never crash
    the app. This is critical for Vercel serverless where:
      - The filesystem is read-only (SQLite will fail → gracefully degrade)
      - Concurrent cold starts can race on CREATE TABLE (idempotent DDL handles it)
      - DATABASE_URL may not be set in every env
    """
    try:
        logger.info("Initializing Database...")
        await init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.warning("DB init failed (non-fatal on Vercel/read-only fs): %s", e)

    try:
        logger.info("Seeding Chakravyooh demo zones and users...")
        async with async_session_maker() as db:
            await seed_demo_zones(db)
        logger.info("Seeding complete.")
    except Exception as e:
        logger.warning("Seeding failed (non-fatal): %s", e)

    mode = "REMOTE/Groq" if settings.GROQ_API_KEY else "LOCAL/Native-ML"
    logger.info("Pukar backend live — mode=%s env=%s", mode, settings.ENVIRONMENT)

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
# AND rewrites all internal URLs so every sub-asset (JS, CSS, tiles, images)
# also flows through /scorpio_feed/ — nothing goes directly to mosdac.gov.in.
# ──────────────────────────────────────────────────────────────────────────

import re as _re
import httpx
from fastapi import Request
from fastapi.responses import Response

_MOSDAC_BASE = "https://mosdac.gov.in/scorpio"
_MOSDAC_ORIGIN = "https://mosdac.gov.in"
_STRIP_HEADERS = {
    "x-frame-options", "content-security-policy",
    "x-content-type-options", "strict-transport-security",
    "content-encoding",  # we decode before rewriting; don't lie about encoding
}
_PROXY_TIMEOUT = 20.0
_TEXT_TYPES = ("text/html", "text/javascript", "application/javascript", "text/css", "application/json")


def _rewrite_urls(text: str) -> str:
    """
    Replace every reference to mosdac.gov.in/scorpio with /scorpio_feed
    so all sub-assets (JS, CSS, WMS tile URLs, fonts, images) are fetched
    through our proxy and inherit its permissive CORS/frame headers.
    """
    # Absolute https/http
    text = text.replace("https://mosdac.gov.in/scorpio/", "/scorpio_feed/")
    text = text.replace("https://mosdac.gov.in/scorpio",  "/scorpio_feed")
    text = text.replace("http://mosdac.gov.in/scorpio/",  "/scorpio_feed/")
    text = text.replace("http://mosdac.gov.in/scorpio",   "/scorpio_feed")

    # Root-relative /scorpio/* paths (inside quotes, parens, or after =)
    text = text.replace('"/scorpio/',  '"/scorpio_feed/')
    text = text.replace("'/scorpio/",  "'/scorpio_feed/")
    text = text.replace('(/scorpio/',  '(/scorpio_feed/')
    text = text.replace('=/scorpio/',  '=/scorpio_feed/')
    text = text.replace('url(/scorpio/', 'url(/scorpio_feed/')

    # Fix <base href> so the browser resolves relative imports through our proxy
    text = _re.sub(
        r'<base\s+href=["\'][^"\']*["\']',
        '<base href="/scorpio_feed/">',
        text,
        flags=_re.IGNORECASE,
    )
    return text


async def _proxy_mosdac(upstream_url: str, request: Request) -> Response:
    """Fetch upstream URL, strip frame/CORS-hostile headers, rewrite URLs, return to browser."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_PROXY_TIMEOUT) as client:
            upstream_headers = {
                "User-Agent": "Mozilla/5.0 (compatible; PukarProxy/1.0; ISRO-CHAKRAVYOOH)",
                "Accept": request.headers.get("accept", "*/*"),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://mosdac.gov.in/",
                "Accept-Encoding": "identity",   # disable gzip so we can rewrite text cleanly
            }
            resp = await client.get(upstream_url, headers=upstream_headers)

            # Build clean response headers
            clean_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in _STRIP_HEADERS
            }
            clean_headers["X-Frame-Options"] = "ALLOWALL"
            clean_headers["Access-Control-Allow-Origin"] = "*"
            clean_headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"

            content_type = resp.headers.get("content-type", "text/html")
            content = resp.content

            # URL-rewrite text responses so every sub-asset routes through our proxy
            if any(t in content_type for t in _TEXT_TYPES):
                try:
                    text = content.decode("utf-8", errors="replace")
                    text = _rewrite_urls(text)
                    content = text.encode("utf-8")
                    # Remove stale content-length; FastAPI will recalculate
                    clean_headers.pop("content-length", None)
                    clean_headers.pop("Content-Length", None)
                except Exception as rw_err:
                    logger.warning("MOSDAC URL rewrite error: %s", rw_err)

            return Response(
                content=content,
                status_code=resp.status_code,
                headers=clean_headers,
                media_type=content_type,
            )

    except httpx.TimeoutException:
        logger.warning("MOSDAC proxy timeout → %s", upstream_url)
        return Response(
            content=(
                "<html><body style='background:#0a0f1e;color:#64748b;"
                "font-family:monospace;display:flex;align-items:center;"
                "justify-content:center;height:100vh;flex-direction:column;gap:1rem'>"
                "<div style='font-size:2rem'>⚡</div>"
                "<h2 style='margin:0'>MOSDAC SCORPIO — Feed Temporarily Unavailable</h2>"
                "<p style='color:#475569'>ISRO server did not respond. Auto-retrying in 8s…</p>"
                "<script>setTimeout(()=>location.reload(),8000)</script>"
                "</body></html>"
            ),
            status_code=504,
            media_type="text/html",
        )
    except Exception as e:
        logger.error("MOSDAC proxy error: %s", e)
        return Response(
            content=(
                "<html><body style='background:#0a0f1e;color:#ef4444;"
                "font-family:monospace;padding:2rem'>"
                f"<h2>MOSDAC Proxy Error</h2><pre>{str(e)[:300]}</pre>"
                "</body></html>"
            ),
            status_code=502,
            media_type="text/html",
        )


@app.get("/scorpio_feed", include_in_schema=False)
async def scorpio_feed_root(request: Request):
    """Proxy MOSDAC SCORPIO root page with full URL rewriting."""
    return await _proxy_mosdac(_MOSDAC_BASE + "/", request)


@app.get("/scorpio_feed/{path:path}", include_in_schema=False)
async def scorpio_feed_asset(path: str, request: Request):
    """Proxy all MOSDAC SCORPIO sub-assets (JS, CSS, WMS tiles, images) with URL rewriting."""
    return await _proxy_mosdac(f"{_MOSDAC_BASE}/{path}", request)


@app.get("/common/{path:path}", include_in_schema=False)
async def mosdac_common_asset(path: str, request: Request):
    """Proxy MOSDAC shared common assets (/common/js/purify.min.js, etc.)."""
    return await _proxy_mosdac(f"{_MOSDAC_ORIGIN}/common/{path}", request)


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
    """Serve Chakravyooh Command Center UI."""
    for candidate in [
        _FRONTEND_DIR / "static" / "index.html",
        _FRONTEND_DIR / "index.html",
    ]:
        if candidate.exists():
            return FileResponse(str(candidate), media_type="text/html")
    return {"error": "Frontend not found."}


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
