"""Vercel entrypoint: the stateless FrameCue API plus the built frontend.

Vercel's FastAPI support detects a root ``app.py`` that exposes ``app``. The package lives in
``src/``, so it is put on the import path explicitly; this works whether or not the project
itself is pip-installed. Local development uses ``framecue serve`` instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from framecue.serverless import create_serverless_app  # noqa: E402

app = create_serverless_app()
