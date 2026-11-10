"""The real pack through the stateless endpoint (skipped unless PROMOLENS_TEST_ZIP is set)."""

import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from promolens.document import AnalysisDocument
from promolens.evaluator import EvaluationCache
from promolens.pipeline import DEFAULT_CACHE_DIR, analyze_path
from promolens.serverless import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, create_serverless_app

ZIP = os.environ.get("PROMOLENS_TEST_ZIP")
pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(not ZIP, reason="set PROMOLENS_TEST_ZIP to the private screenplay ZIP"),
]
CACHE = Path(__file__).resolve().parents[1] / DEFAULT_CACHE_DIR


def _refuse(self, request):
    raise AssertionError("unexpected network call")


def test_full_pack_through_the_stateless_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    client = TestClient(create_serverless_app(frontend_dir=None))
    data = Path(ZIP).read_bytes()
    files = [("files", ("pack.zip", data, "application/zip"))]
    bundle = EvaluationCache(CACHE).export_bundle() if CACHE.is_dir() else None
    if bundle:
        import json

        files.append(("ai_bundle", ("ai.json", json.dumps(bundle).encode(), "application/json")))
    request = client.build_request("POST", "/api/analyze", files=files)
    assert len(request.read()) < MAX_REQUEST_BYTES
    started = time.perf_counter()
    response = client.send(request)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200 and len(response.content) < MAX_RESPONSE_BYTES
    assert elapsed < 30
    doc = AnalysisDocument.model_validate_json(response.content)
    assert doc.summary.episodes == 50 and doc.summary.candidates == 788
    assert len(doc.events) == 3727 and len(doc.views.coverage.order) == 788
    local = analyze_path(ZIP, cache_dir=CACHE if bundle else None)
    assert set(doc.ai_evaluations) == set(local.ai)
    assert list(doc.views.ai.order) == [e.candidate_id for e in local.views.ai]
    assert list(doc.views.coverage.order) == [e.candidate_id for e in local.views.coverage]
    for cid, j in doc.ai_evaluations.items():
        assert j.weighted_score == local.ai[cid].weighted_score
