"""Stateless deployment app (Vercel): /api/health and /api/analyze. Synthetic data only."""

import importlib.util
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeline_fakes import episode_text, fill_cache, pack_bytes
from promolens import cli, serverless
from promolens.api import ApiConfig, create_app
from promolens.document import AnalysisDocument
from promolens.evaluator import EvaluationCache
from promolens.parser import parse_zip_bytes
from promolens.serverless import MAX_REQUEST_BYTES, MAX_UPLOAD_BYTES, create_serverless_app
from promolens.timecode import parse_timestamp

REPO = Path(__file__).resolve().parents[1]
LINE = "We should check the list."  # the synthetic dialogue; must never reach logs


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, request):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def client():
    return TestClient(create_serverless_app(frontend_dir=None), raise_server_exceptions=False)


def analyze(client, numbers=(1, 2, 3), bundle: dict | None = None, extra_files=()):
    files = [("files", ("pack.zip", pack_bytes(list(numbers)), "application/zip"))]
    if bundle is not None:
        files.append(("ai_bundle", ("ai.json", json.dumps(bundle).encode(), "application/json")))
    return client.post("/api/analyze", files=files + list(extra_files))


def bundle_for(tmp_path, numbers=(1, 2, 3), per_episode=2) -> dict:
    episodes = list(parse_zip_bytes(pack_bytes(list(numbers)), "p.zip").episodes)
    fill_cache(tmp_path / "cache", episodes[:2], per_episode=per_episode)
    return EvaluationCache(tmp_path / "cache").export_bundle()


# --- the analysis document ------------------------------------------------------------------


def test_stateless_analysis_is_complete_and_normalized(client) -> None:
    r = analyze(client)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    doc = AnalysisDocument.model_validate(r.json())  # exact contract
    assert doc.schema_version == "promolens-analysis-v1"
    assert [e.episode_number for e in doc.episodes] == [1, 2, 3]
    assert doc.summary.candidates == len(doc.candidates) and doc.summary.live_ai_calls == 0
    for ep in doc.episodes:
        first, last = ep.event_range
        sliced = doc.events[first:last]
        assert sliced and all(e.episode == ep.episode_number for e in sliced)
        assert [e.start_ms for e in sliced] == sorted(e.start_ms for e in sliced)
    assert len(doc.events) == doc.summary.events  # every event exactly once
    assert len({e.id for e in doc.events}) == len(doc.events)
    assert sum(r.json()["events"][0].keys() == {"id", "episode", "scene", "type", "start_ms",
               "end_ms", "speaker", "annotations", "text"} for _ in [0]) == 1  # fmt: skip
    assert LINE not in json.dumps(r.json()["candidates"])  # text lives only in events


def test_timestamps_and_source_ids_are_exact(client) -> None:
    doc = AnalysisDocument.model_validate(analyze(client).json())
    ids = {e.id for e in doc.events}
    for c in doc.candidates:
        [seg] = c.segments
        assert (seg.start_ms, seg.end_ms) == (c.start_ms, c.end_ms)
        assert parse_timestamp(seg.start_timestamp) == c.start_ms
        assert parse_timestamp(c.end_timestamp) == c.end_ms
        assert set(c.source_events.opening_window) | set(c.source_events.ending_window) <= ids
        assert c.coverage_rank is not None or c.coverage_alternative_of is not None


def test_without_bundle_no_ai_is_shown_or_fabricated(client) -> None:
    doc = AnalysisDocument.model_validate(analyze(client).json())
    assert doc.ai_evaluations == {} and doc.views.ai.order == ()
    assert doc.views.ai.provisional and doc.coverage.ai_evaluated == 0
    assert doc.summary.ai_source == "none" and doc.coverage.cache.source == "none"
    assert all(c.evaluation_status == "not_evaluated" and c.ai_rank is None for c in doc.candidates)
    assert len(doc.views.coverage.order) == len(doc.candidates)
    assert doc.views.coverage.scoring_system == "heuristic_priority_v1"


def test_uploaded_ai_bundle_is_validated_and_kept_separate(client, tmp_path) -> None:
    bundle = bundle_for(tmp_path)
    doc = AnalysisDocument.model_validate(analyze(client, bundle=bundle).json())
    assert len(doc.ai_evaluations) == 4 and doc.summary.ai_source == "uploaded_bundle"
    assert set(doc.views.ai.order) == set(doc.ai_evaluations)
    assert doc.views.ai.scoring_system == "ai_weighted_v1" and doc.views.ai.score_scale_max == 100
    for cid, judgement in doc.ai_evaluations.items():
        c = next(x for x in doc.candidates if x.candidate_id == cid)
        assert c.evaluation_status == "ai_evaluated"
        assert judgement.hook_explanation and set(judgement.hook_event_ids) <= {
            e.id for e in doc.events
        }
    # the same bundle does not apply to a different screenplay (checksums differ)
    other = client.post("/api/analyze", files=[
        ("files", ("e.txt", episode_text(1, line="Different words.").encode(), "text/plain")),
        ("ai_bundle", ("ai.json", json.dumps(bundle).encode(), "application/json")),
    ]).json()  # fmt: skip
    assert other["ai_evaluations"] == {} and other["coverage"]["cache"]["used"] == 0


def test_tampered_bundle_entry_is_rejected(client, tmp_path) -> None:
    bundle = bundle_for(tmp_path)
    key = sorted(bundle["entries"])[0]
    bundle["entries"][key]["assessment"]["hook_event_ids"] = ["E001-S01-9999"]
    doc = analyze(client, bundle=bundle).json()
    assert len(doc["ai_evaluations"]) == 3
    assert doc["coverage"]["cache"]["invalid_on_revalidation"] == [key]


@pytest.mark.parametrize(
    ("bundle_bytes", "message"),
    [
        (b"not json", "not valid JSON"),
        (json.dumps({"format": "other"}).encode(), "not a promolens-ai-bundle-v1"),
        (json.dumps({"format": "promolens-ai-bundle-v1", "entries": {"../x": {}}}).encode(),
         "64-character hex"),
    ],
)  # fmt: skip
def test_invalid_bundles_are_rejected(client, bundle_bytes, message) -> None:
    files = [
        ("files", ("pack.zip", pack_bytes([1]), "application/zip")),
        ("ai_bundle", ("ai.json", bundle_bytes, "application/json")),
    ]
    r = client.post("/api/analyze", files=files)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_ai_bundle"
    assert message in r.json()["error"]["message"]


def test_deterministic_apart_from_time(client) -> None:
    a, b = analyze(client).json(), analyze(client).json()
    for d in (a, b):
        d["summary"].pop("analyzed_at")
        d["summary"].pop("timings_ms")
    assert a == b


# --- validation and limits ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "status", "code"),
    [
        ([("files", ("x.pdf", b"%PDF", "application/pdf"))], 400, "unsupported_file_type"),
        ([("files", ("x.zip", b"nope", "application/zip"))], 400, "not_a_zip"),
        ([("files", ("e.txt", b"no episode", "text/plain"))], 422, "no_valid_episodes"),
    ],
)
def test_upload_validation(client, files, status, code) -> None:
    r = client.post("/api/analyze", files=files)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_oversized_requests_are_rejected_with_cli_guidance(client) -> None:
    big = b"x" * (MAX_UPLOAD_BYTES + 10)  # body below the 4.4 MB guard, content above 4 MB
    r = client.post("/api/analyze", files=[("files", ("big.txt", big, "text/plain"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    assert "promolens analyze" in r.json()["error"]["message"]
    huge = b"x" * (MAX_REQUEST_BYTES + 10)  # rejected from Content-Length before parsing
    r = client.post("/api/analyze", files=[("files", ("huge.txt", huge, "text/plain"))])
    assert r.status_code == 413 and "promolens analyze" in r.json()["error"]["message"]
    assert MAX_REQUEST_BYTES < 4_500_000 and MAX_UPLOAD_BYTES < MAX_REQUEST_BYTES


def test_episode_and_response_limits(client, monkeypatch) -> None:
    tight = serverless.SERVERLESS_LIMITS.__class__(
        **{**serverless.SERVERLESS_LIMITS.__dict__, "max_episodes": 2}
    )
    monkeypatch.setattr(serverless, "SERVERLESS_LIMITS", tight)
    assert analyze(client).json()["error"]["code"] == "too_many_episodes"
    small = tight.__class__(**{**tight.__dict__, "max_episodes": None, "max_response_bytes": 1000})
    monkeypatch.setattr(serverless, "SERVERLESS_LIMITS", small)
    r = analyze(client)
    assert r.status_code == 413 and r.json()["error"]["code"] == "analysis_too_large"


def test_response_size_is_far_below_the_platform_limit(client) -> None:
    r = analyze(client)
    assert len(r.content) < serverless.MAX_RESPONSE_BYTES < 4_500_000


# --- statelessness, privacy, logging --------------------------------------------------------


def test_no_persistent_state_or_stateful_routes(client, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert analyze(client).status_code == 200
    assert sorted(p.name for p in tmp_path.rglob("*")) == before  # nothing written
    assert client.get("/api/runs/AAAAAAAAAAAAAAAAAAAAAA").status_code == 404
    assert client.post("/api/runs", files=[("files", ("a.txt", b"x"))]).status_code in (404, 405)
    assert not hasattr(client.app.state, "store")


def test_no_screenplay_text_in_logs(client, caplog, tmp_path) -> None:
    caplog.set_level(logging.DEBUG)
    analyze(client, bundle=bundle_for(tmp_path))
    client.post("/api/analyze", files=[("files", ("x.zip", b"nope"))])
    assert LINE not in caplog.text and "synthetic logline" not in caplog.text.lower()


def test_health_is_offline_and_stateless(client) -> None:
    h = client.get("/api/health").json()
    assert h["offline"] and h["stateless"] and not h["live_ai"]
    assert h["max_upload_bytes"] == MAX_UPLOAD_BYTES


# --- Vercel entrypoint, frontend routing, packaging ------------------------------------------


def test_vercel_entrypoint_imports_and_exposes_only_stateless_routes() -> None:
    spec = importlib.util.spec_from_file_location("vercel_entry", REPO / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module.app, FastAPI)
    paths = {getattr(r, "path", "") for r in module.app.routes}
    assert {"/api/health", "/api/analyze"} <= paths and "/api/runs" not in paths


def test_frontend_is_served_with_spa_fallback_and_api_priority(tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>placeholder</html>", "utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", "utf-8")
    c = TestClient(create_serverless_app(frontend_dir=dist))
    assert c.get("/").text == "<html>placeholder</html>"
    deep = c.get("/episodes/7/promos", headers={"Accept": "text/html"})
    assert deep.status_code == 200 and "placeholder" in deep.text  # deep link -> index.html
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/health").json()["status"] == "ok"  # API wins over frontend
    missing_api = c.get("/api/does-not-exist", headers={"Accept": "text/html"})
    assert "placeholder" not in missing_api.text


def test_missing_frontend_on_vercel_keeps_api_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    c = TestClient(create_serverless_app(frontend_dir=tmp_path / "missing"))
    assert c.get("/api/health").status_code == 200


def test_vercelignore_excludes_private_files_and_keeps_the_app() -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git is needed to evaluate ignore rules")
    tree = [
        "app.py", "pyproject.toml", "README.md", "vercel.json", ".python-version",
        "src/promolens/api.py", "frontend/package.json", "frontend/src/main.ts",
        "frontend/index.html",
        # must be excluded:
        "pack.zip", "brief.pdf", ".env", ".env.local", "data/private/x.txt", "runs/cache/a.json",
        "runs/ai_bundle.json", "outputs/x.csv", "episode-01.txt", "src/promolens/__pycache__/x.pyc",
        "frontend/node_modules/vite/index.js", "frontend/dist/index.html", "tests/test_x.py",
        "docs/PLAN.md", "frontend/src/secrets.json", "src/pack.zip", "frontend/review/notes.txt",
    ]  # fmt: skip
    keep = set(tree[:9])
    repo = Path(__file__).parent / ".." / ".pytest_vercelignore"
    repo = repo.resolve()
    shutil.rmtree(repo, ignore_errors=True)
    try:
        repo.mkdir()
        subprocess.run([git, "init", "-q"], cwd=repo, check=True)
        (repo / ".gitignore").write_text((REPO / ".vercelignore").read_text("utf-8"), "utf-8")
        for name in tree:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("x", "utf-8")
        result = subprocess.run(
            [git, "-c", "core.quotepath=off", "check-ignore", "--stdin", "--no-index"],
            cwd=repo, input=("\n".join(tree) + "\n").encode(), capture_output=True,
        )  # fmt: skip
        ignored = set(result.stdout.decode().split())
        assert keep.isdisjoint(ignored), keep & ignored
        assert set(tree) - keep <= ignored, (set(tree) - keep) - ignored
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_vercel_config_is_minimal_and_points_at_the_entrypoint() -> None:
    config = json.loads((REPO / "vercel.json").read_text("utf-8"))
    assert set(config) <= {"$schema", "buildCommand", "functions", "rewrites"}
    assert "app.py" in config["functions"] and config["functions"]["app.py"]["maxDuration"] <= 300
    assert "builds" not in config and "services" not in config and "public" not in config


def test_vercel_spa_rewrite_never_captures_api_paths() -> None:
    """Static files live on the CDN (cdn = true), so the function has no index.html fallback;
    the one rewrite serves deep links. It must not swallow /api/* (filesystem wins first)."""
    (rewrite,) = json.loads((REPO / "vercel.json").read_text("utf-8"))["rewrites"]
    assert rewrite["destination"] == "/index.html"
    pattern = re.compile("^" + rewrite["source"] + "$")
    for path in ("/review", "/review/abc/def", "/"):
        assert pattern.match(path), path
    for path in ("/api/health", "/api/analyze", "/api/x/y", "/api"):
        assert not pattern.match(path), path
    assert (REPO / ".python-version").read_text("utf-8").strip() == "3.12"


# --- local workflows keep working ------------------------------------------------------------


def test_local_app_also_offers_stateless_analyze(tmp_path) -> None:
    local = TestClient(create_app(ApiConfig(store_dir=tmp_path / "s", cache_dir=None)))
    r = local.post("/api/analyze", files=[("files", ("pack.zip", pack_bytes([1, 2])))])
    assert r.status_code == 200 and r.json()["summary"]["episodes"] == 2
    assert not (tmp_path / "s").exists()  # analyze stores nothing even in the local app


def test_cli_export_ai_writes_a_private_bundle(tmp_path, capsys) -> None:
    episodes = list(parse_zip_bytes(pack_bytes([1]), "p.zip").episodes)
    fill_cache(tmp_path / "cache", episodes, per_episode=1)
    out = tmp_path / "bundle.json"
    assert cli.main(["export-ai", "--cache-dir", str(tmp_path / "cache"), "--out", str(out)]) == 0
    bundle = json.loads(out.read_text("utf-8"))
    assert bundle["format"] == "promolens-ai-bundle-v1" and len(bundle["entries"]) == 1
    assert "PRIVATE" in capsys.readouterr().out
    assert cli.main(["export-ai", "--cache-dir", str(tmp_path / "none")]) == 2


# --- frontend contract and public sample ------------------------------------------------------


def test_committed_openapi_matches_the_stateless_app() -> None:
    """frontend/src/api/openapi.json feeds the generated TypeScript types; it must not drift."""
    committed = json.loads((REPO / "frontend" / "src" / "api" / "openapi.json").read_text("utf-8"))
    assert committed == json.loads(json.dumps(create_serverless_app(frontend_dir=None).openapi()))


def test_public_sample_is_fictional_valid_and_analyzable() -> None:
    from promolens.parser import parse_path
    from promolens.pipeline import analyze_path

    sample = REPO / "frontend" / "public" / "sample"
    files = sorted(p.name for p in sample.glob("*.txt"))
    assert files == ["harbor-light-01.txt", "harbor-light-02.txt", "harbor-light-03.txt"]
    for p in sample.glob("*.txt"):
        assert "Fictional sample written for PromoLens" in p.read_text("utf-8")
    result = parse_path(sample)
    assert len(result.episodes) == 3 and not result.issues
    assert len(analyze_path(sample, cache_dir=None).by_id) > 30
