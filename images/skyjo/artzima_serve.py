"""artzima wrapper: serve the Skyjo API and its built frontend from ONE origin.

Upstream (DevArtech/skynet) is untouched. This module imports the upstream
FastAPI app and mounts the Vite build (compiled with VITE_API_BASE="", so the
browser calls same-origin /api/...) at "/".

The mount is added last, so every route upstream registers (all under /api,
including /api/health) still matches first. Run with:

    uvicorn artzima_serve:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from web.backend.main import app

STATIC_DIR = Path(os.environ.get("SKYJO_STATIC_DIR", Path(__file__).resolve().with_name("static")))

if not (STATIC_DIR / "index.html").is_file():
    raise RuntimeError(f"frontend build not found: {STATIC_DIR}/index.html")

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")

__all__ = ["app"]
