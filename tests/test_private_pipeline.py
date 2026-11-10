"""End-to-end checks on the confidential pack (skipped unless PROMOLENS_TEST_ZIP is set).

Assertions are about consistency and structure, never about screenplay content. The AI cache
is the local private one, so AI counts are compared across outputs instead of hard-coded.
"""

import csv
import io
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from promolens.api import ApiConfig, create_app
from promolens.exports import write_exports
from promolens.pipeline import DEFAULT_CACHE_DIR, analyze_path, coverage
from promolens.ranking import PromoFilter, page
from promolens.timecode import parse_timestamp

ZIP = os.environ.get("PROMOLENS_TEST_ZIP")
pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(not ZIP, reason="set PROMOLENS_TEST_ZIP to the private screenplay ZIP"),
]
REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / DEFAULT_CACHE_DIR


@pytest.fixture(scope="module")
def run():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        started = time.perf_counter()
        result = analyze_path(ZIP, cache_dir=CACHE)
        result.elapsed = time.perf_counter() - started
        return result


@pytest.fixture(scope="module")
def exported(run, tmp_path_factory):
    out = tmp_path_factory.mktemp("exports")
    return out, write_exports(run, out, raw_dir=None)


def test_full_pack_counts_and_speed(run) -> None:
    assert len(run.episodes) == 50 and sorted(run.episodes) == list(range(1, 51))
    assert len(run.by_id) == 788
    assert sum(len(e.events) for e in run.episodes.values()) == 3727
    assert run.elapsed < 20
    cov = coverage(run)
    assert cov.total_candidates == 788
    assert cov.ai_evaluated + cov.not_evaluated + cov.failed == 788
    assert cov.cache.used == cov.ai_evaluated and not cov.cache.invalid_on_revalidation


def test_every_episode_in_coverage_view_and_ai_view_is_ai_only(run) -> None:
    cov_view = page(run, "coverage", PromoFilter(include_alternatives=True), limit=10_000)
    assert cov_view.total == 788
    assert {p.episode_number for p in cov_view.items if p.rank} == set(range(1, 51))
    assert all(p.score.system == "heuristic_priority_v1" and p.hook_explanation is None
               for p in cov_view.items)  # fmt: skip
    ai_view = page(run, "ai", PromoFilter(include_alternatives=True), limit=10_000)
    assert {p.candidate_id for p in ai_view.items} == set(run.ai)
    assert ai_view.provisional == (len(run.ai) < 788)
    for p in ai_view.items:
        e = run.ai[p.candidate_id]
        assert p.score.value == e.weighted_score and e.provenance.prompt_version == "promo-eval-v3"
        events = {x.event_id for x in run.episodes[p.episode_number].events}
        assert set(p.hook_event_ids) <= events and set(p.cliffhanger_event_ids) <= events


def test_exports_are_valid_and_exact(run, exported) -> None:
    out, manifest = exported
    records = [json.loads(x) for x in (out / "candidates.jsonl").read_text("utf-8").splitlines()]
    assert len(records) == 788 and len({r["candidate_id"] for r in records}) == 788
    for r in records:
        c = run.by_id[r["candidate_id"]]
        seg = r["segments"][0]
        assert (seg["start_ms"], seg["end_ms"]) == (c.start_ms, c.end_ms)
        assert parse_timestamp(seg["start_timestamp"]) == c.start_ms
        assert parse_timestamp(seg["end_timestamp"]) == c.end_ms
        assert (r["ai"] is not None) == (c.candidate_id in run.ai)
    ai_lines = (out / "ai_evaluations.jsonl").read_text("utf-8").splitlines()
    assert len(ai_lines) == len(run.ai)
    ai_csv = list(csv.DictReader(io.StringIO((out / "ai_recommendations.csv").read_text("utf-8"))))
    cov_csv = list(
        csv.DictReader(io.StringIO((out / "coverage_candidates.csv").read_text("utf-8")))
    )
    assert len(ai_csv) == len(run.ai) and len(cov_csv) == 788
    assert all(r["score_system"] == "ai_weighted_v1" for r in ai_csv)
    assert all(r["hook_explanation"] == "" for r in cov_csv)
    episodes = json.loads((out / "episodes.json").read_text("utf-8"))
    assert [e["episode_number"] for e in episodes] == list(range(1, 51))
    assert manifest.run.coverage.ai_evaluated == len(run.ai)


def test_api_matches_exports_on_the_real_pack(run, exported, tmp_path) -> None:
    out, _ = exported
    client = TestClient(create_app(ApiConfig(store_dir=tmp_path / "store", cache_dir=CACHE,
                                             raw_dir=None)))  # fmt: skip
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        data = Path(ZIP).read_bytes()
        summary = client.post("/api/runs", files={"files": ("pack.zip", data)}).json()
        run_id = summary["run_id"]
        assert summary["episodes"] == 50 and summary["candidates"] == 788
        assert summary["coverage"]["ai_evaluated"] == len(run.ai)
        ranked = _collect(client, run_id, "ai")
    exported_ai = json.loads((out / "rankings_ai.json").read_text("utf-8"))["items"]
    assert ranked == [p["candidate_id"] for p in exported_ai if p["rank"] is not None]


def _collect(client, run_id, view) -> list[str]:
    ids, offset = [], 0
    while True:
        params = {"view": view, "limit": 200, "offset": offset}
        page_ = client.get(f"/api/runs/{run_id}/promos", params=params).json()
        ids += [p["candidate_id"] for p in page_["items"]]
        offset += 200
        if offset >= page_["total"]:
            return ids


def _refuse(self, request):
    raise AssertionError("unexpected network call")
