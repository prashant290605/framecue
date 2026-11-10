"""Write the stateless API's OpenAPI schema to src/api/openapi.json (input for gen:types)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from promolens.serverless import create_serverless_app  # noqa: E402

schema = create_serverless_app(frontend_dir=None).openapi()
out = Path(__file__).resolve().parents[1] / "src" / "api" / "openapi.json"
out.write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n", encoding="utf-8")
print("wrote", out.relative_to(ROOT))
