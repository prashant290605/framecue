"""Build frontend test fixtures from the fictional sample via the real stateless endpoint.

- sample-document.json: the sample analysed with no AI bundle (the public demo case).
- sample-document-ai.json: the same sample plus a SYNTHETIC AI bundle produced by the real
  evaluator against a mocked model (tests/pipeline_fakes.py). The judgements are canned test
  text, not real model output, and are used only by tests.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

from framecue.evaluator import EvaluationCache  # noqa: E402
from framecue.parser import parse_path  # noqa: E402
from framecue.serverless import create_serverless_app  # noqa: E402
from pipeline_fakes import fill_cache  # noqa: E402

SAMPLE = ROOT / "frontend" / "public" / "sample"
OUT = ROOT / "frontend" / "src" / "test" / "fixtures"
client = TestClient(create_serverless_app(frontend_dir=None))
files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted(SAMPLE.glob("*.txt"))]


def save(name: str, response) -> None:
    assert response.status_code == 200, response.text
    doc = response.json()
    doc["summary"]["analyzed_at"] = "2026-11-10T00:00:00+00:00"  # stable fixture
    (OUT / name).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(name, len(doc["candidates"]), "candidates,", len(doc["ai_evaluations"]), "AI")


save("sample-document.json", client.post("/api/analyze", files=files))
with tempfile.TemporaryDirectory() as tmp:
    episodes = list(parse_path(SAMPLE).episodes)
    from framecue.candidates import generate_candidates

    pattern = [(4, 5, 4, 4), (3, 2, 3, 3), (5, 4, 3, 4), (2, 1, 2, 3), (4, 3, 4, 3)]
    scores = {
        c.candidate_id: pattern[i % len(pattern)]
        for e in episodes[:2]
        for i, c in enumerate(list(generate_candidates(e).shortlist)[:5])
    }
    fill_cache(Path(tmp), episodes[:2], per_episode=5, scores=scores)
    bundle = EvaluationCache(Path(tmp)).export_bundle()
    blob = ("ai_bundle", ("bundle.json", json.dumps(bundle).encode(), "application/json"))
    save("sample-document-ai.json", client.post("/api/analyze", files=[*files, blob]))
