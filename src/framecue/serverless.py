"""Stateless FastAPI app for Vercel Functions (one project, one URL).

Why there is no run store here: a Vercel function's filesystem is read-only apart from a
temporary ``/tmp``. Each request may land on a different instance, so a stored run could
vanish between requests. The deployment therefore exposes only:

    GET  /api/health
    POST /api/analyze    multipart: "files" (one .zip or .txt files), optional "ai_bundle"

``/api/analyze`` returns the complete ``AnalysisDocument`` in one response. The browser keeps
it in memory. Nothing is written server-side: uploads are read into memory and parsed there.

Limits are kept under Vercel's 4.5 MB request and response bodies. Larger inputs get a clear
413 pointing to the local CLI, which has no such limit.

No model is ever called. No AI cache ships with the deployment (``.vercelignore``). AI-reviewed
results appear only when the user uploads a private ``ai_bundle`` exported by
``framecue export-ai``. Its entries are used only if they match the uploaded screenplay
exactly and their evidence re-validates.

The built frontend (``frontend/dist``) is served through FastAPI's ``app.frontend()``. On
Vercel those files are promoted to the CDN at build time, so static assets never invoke
Python. API routes always win over frontend paths, and deep links fall back to
``index.html``.
"""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import Response

from framecue import __version__
from framecue.api import (
    AnalyzeLimits,
    ApiError,
    _origins_from_env,
    analyze_request,
    install_common,
)
from framecue.document import AnalysisDocument

# Vercel: 4.5 MB per request body. Keep the whole multipart body under 4.4 MB, and the file
# content under 4.0 MB, so the platform limit is never what rejects a request.
MAX_REQUEST_BYTES = 4_400_000
MAX_UPLOAD_BYTES = 4_000_000
MAX_RESPONSE_BYTES = 4_300_000
CLI_HINT = '; analyze larger inputs locally with the CLI: framecue analyze "<file>" --out <folder>'
SERVERLESS_LIMITS = AnalyzeLimits(
    max_upload_bytes=MAX_UPLOAD_BYTES,
    max_files=120,
    max_zip_entries=600,
    max_decompressed_bytes=40 * 1024 * 1024,
    max_episodes=200,
    max_response_bytes=MAX_RESPONSE_BYTES,
    too_large_hint=CLI_HINT,
)
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
log = logging.getLogger("framecue.serverless")


def create_serverless_app(frontend_dir: Path | None = DEFAULT_FRONTEND_DIR) -> FastAPI:
    app = FastAPI(
        title="FrameCue (stateless)",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_common(
        app,
        allowed_origins=_origins_from_env(),
        max_body_bytes=MAX_REQUEST_BYTES,
        too_large_message=f"request exceeds {MAX_REQUEST_BYTES / 1_000_000:.1f} MB{CLI_HINT}",
    )

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "offline": True,
            "live_ai": False,
            "stateless": True,
            "max_upload_bytes": MAX_UPLOAD_BYTES,
        }

    @app.post("/api/analyze", response_model=AnalysisDocument)
    def analyze(
        files: Annotated[list[UploadFile], File()],
        ai_bundle: Annotated[UploadFile | None, File()] = None,
    ) -> Response:
        return analyze_request(files, ai_bundle, SERVERLESS_LIMITS, None, None)

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(rest: str) -> None:
        """Unknown /api paths get a JSON 404, never the SPA's index.html."""
        raise ApiError(404, "not_found", "unknown API route")

    if frontend_dir is not None:
        if Path(frontend_dir).is_dir():
            app.frontend("/", directory=str(frontend_dir), fallback="index.html")
        elif os.environ.get("VERCEL"):  # keep the API up; the build step owns the frontend
            log.warning("frontend build directory missing; serving API routes only")
    return app
