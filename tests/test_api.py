"""FastAPI backend via TestClient. Synthetic inputs only; real network calls are refused."""

import csv
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from pipeline_fakes import episode_text, fill_cache, pack_bytes
from promolens import api as api_module
from promolens.api import ApiConfig, create_app
from promolens.parser import parse_zip_bytes

ORIGIN = "http://localhost:5173"
SECRET = "sk-or-gsk_SHOULD-NEVER-APPEAR-0000"


@pytest.fixture(autouse=True)
def guarded(monkeypatch):
    def refuse(self, request):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setenv("PROMOLENS_LLM_API_KEY", SECRET)

    def no_client(*a, **k):
        raise AssertionError("the API must never create a model client")

    monkeypatch.setattr("promolens.llm_client.ChatCompletionsClient.__init__", no_client)


def make_client(tmp_path, **overrides) -> TestClient:
    config = ApiConfig(
        store_dir=tmp_path / "store",
        cache_dir=overrides.pop("cache", tmp_path / "no-cache"),
        raw_dir=None,
        allowed_origins=(ORIGIN,),
        **overrides,
    )
    return TestClient(create_app(config), raise_server_exceptions=False)


def upload(client, files):
    return client.post("/api/runs", files=[("files", f) for f in files])


def zip_file(numbers=(1, 2, 3), extra=None):
    return ("pack.zip", pack_bytes(list(numbers), extra), "application/zip")


def test_health_reports_offline() -> None:
    client = TestClient(create_app(ApiConfig(store_dir="unused", cache_dir=None)))
    assert client.get("/api/health").json() == {
        "status": "ok", "version": client.app.version, "offline": True, "live_ai": False,
    }  # fmt: skip


def test_full_flow_zip_upload_summary_promos_detail_export(tmp_path) -> None:
    client = make_client(tmp_path)
    r = upload(client, [zip_file()])
    assert r.status_code == 201
    summary = r.json()
    run_id = summary["run_id"]
    assert len(run_id) == 22 and summary["episodes"] == 3 and summary["live_ai_calls"] == 0
    assert summary["coverage"]["ai_evaluated"] == 0 and summary["input"]["kind"] == "zip"
    assert client.get(f"/api/runs/{run_id}").json() == summary
    episodes = client.get(f"/api/runs/{run_id}/episodes").json()
    assert [e["episode_number"] for e in episodes] == [1, 2, 3]
    ai = client.get(f"/api/runs/{run_id}/promos", params={"view": "ai"}).json()
    assert ai["total"] == 0 and ai["provisional"] and ai["scoring_system"] == "ai_weighted_v1"
    cov = client.get(f"/api/runs/{run_id}/promos", params={"view": "coverage", "limit": 5}).json()
    assert cov["scoring_system"] == "heuristic_priority_v1" and len(cov["items"]) == 5
    first = cov["items"][0]
    assert first["rank"] == 1 and first["segments"][0]["start_timestamp"]
    assert first["hook_explanation"] is None and first["evaluation_status"] == "not_evaluated"
    detail = client.get(f"/api/runs/{run_id}/promos/{first['candidate_id']}").json()
    assert detail["ai"] is None and detail["coverage_rank"] == 1 and detail["ai_rank"] is None
    events = detail["timeline"]["events"]
    assert events and any(e["in_clip"] for e in events) and any(not e["in_clip"] for e in events)
    assert {"opening_window"} <= {r for e in events for r in e["roles"]}
    manifest = client.get(f"/api/runs/{run_id}/export").json()
    assert {f["name"] for f in manifest["files"]} >= {"candidates.jsonl", "coverage_candidates.csv"}
    csv_response = client.get(f"/api/runs/{run_id}/export/coverage_candidates.csv")
    assert csv_response.status_code == 200 and csv_response.headers["content-type"].startswith(
        "text/csv"
    )
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    api_ids = [p["candidate_id"] for p in _all(client, run_id, "coverage")]
    assert [r["candidate_id"] for r in rows if r["rank"]] == api_ids  # API matches export
    assert csv_response.headers["cache-control"] == "no-store"


def test_api_includes_cached_ai_and_keeps_views_separate(tmp_path) -> None:
    episodes = list(parse_zip_bytes(pack_bytes([1, 2, 3]), "pack.zip").episodes)
    cache = tmp_path / "cache"
    _fill_without_guard(cache, episodes[:1])
    client = make_client(tmp_path, cache=cache)
    run_id = upload(client, [zip_file()]).json()["run_id"]
    ai = client.get(f"/api/runs/{run_id}/promos", params={"view": "ai"}).json()
    assert ai["total"] >= 1 and all(p["score"]["system"] == "ai_weighted_v1" for p in ai["items"])
    pick = ai["items"][0]
    assert pick["hook_explanation"] and pick["hook_event_ids"]
    detail = client.get(f"/api/runs/{run_id}/promos/{pick['candidate_id']}").json()
    assert detail["ai"]["weighted_score"] == pick["score"]["value"]
    assert detail["baseline"]["priority"] <= 1
    cov = _all(client, run_id, "coverage", alternatives=True)
    row = next(p for p in cov if p["candidate_id"] == pick["candidate_id"])
    assert row["score"]["system"] == "heuristic_priority_v1" and row["ai_score_available"]
    assert row["hook_explanation"] is None


def test_txt_uploads_and_new_episode(tmp_path) -> None:
    client = make_client(tmp_path)
    files = [(f"ep{n}.txt", episode_text(n).encode(), "text/plain") for n in (8, 9)]
    r = upload(client, files)
    assert (
        r.status_code == 201 and r.json()["episodes"] == 2 and r.json()["input"]["kind"] == "files"
    )
    single = upload(client, [files[0]])
    assert single.status_code == 201 and single.json()["input"]["kind"] == "txt"


@pytest.mark.parametrize(
    ("files", "status", "code"),
    [
        ([("notes.pdf", b"%PDF", "application/pdf")], 400, "unsupported_file_type"),
        ([("fake.zip", b"not a zip at all", "application/zip")], 400, "not_a_zip"),
        ([zip_file(), ("e.txt", b"x", "text/plain")], 400, "mixed_inputs"),
        ([("e.txt", b"\xff\xfe bad bytes", "text/plain")], 422, "no_valid_episodes"),
        ([("e.txt", b"just some words", "text/plain")], 422, "no_valid_episodes"),
    ],
)
def test_invalid_uploads_get_clear_sanitized_errors(tmp_path, files, status, code) -> None:
    r = upload(make_client(tmp_path), files)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert "Traceback" not in r.text


def test_unsafe_zip_entries_are_rejected(tmp_path) -> None:
    client = make_client(tmp_path)
    only_unsafe = pack_bytes([], extra={"../../escape.txt": episode_text(1).encode()})
    r = upload(client, [("bad.zip", only_unsafe, "application/zip")])
    assert r.status_code == 422 and "unsafe_archive_entry" in r.json()["error"]["issue_codes"]
    assert not (tmp_path / "escape.txt").exists()
    mixed = upload(client, [zip_file((1,), extra={"/abs/evil.txt": b"x"})])
    assert mixed.status_code == 201
    assert mixed.json()["parse"]["issue_codes"].get("unsafe_archive_entry") == 1


def test_oversized_uploads_are_refused(tmp_path) -> None:
    client = make_client(tmp_path, max_upload_bytes=2_000)
    r = upload(client, [zip_file()])
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    many = make_client(tmp_path, max_files=2)
    files = [(f"e{n}.txt", episode_text(n).encode(), "text/plain") for n in (1, 2, 3)]
    assert upload(many, files).json()["error"]["code"] == "too_many_files"
    assert client.post("/api/runs").status_code == 422  # no files field at all


def test_unknown_ids_and_bad_parameters(tmp_path) -> None:
    client = make_client(tmp_path)
    run_id = upload(client, [zip_file()]).json()["run_id"]
    for path in ("/api/runs/AAAAAAAAAAAAAAAAAAAAAA", "/api/runs/../../etc", "/api/runs/x/episodes"):
        assert client.get(path).status_code == 404
    assert (
        client.get(f"/api/runs/{run_id}/promos/C001-0000000-0000001").json()["error"]["code"]
        == "candidate_not_found"
    )
    for name in ("..%2Fmanifest.json", "pyproject.toml", "run_state.json"):
        assert client.get(f"/api/runs/{run_id}/export/{name}").status_code == 404
    bad = client.get(f"/api/runs/{run_id}/promos", params={"view": "everything"})
    assert bad.status_code == 422 and bad.json()["error"]["fields"][0]["loc"] == ["query", "view"]
    assert "everything" not in bad.text  # input values are not echoed
    for params in ({"limit": 0}, {"limit": 1000}, {"offset": -1}, {"episode": 0},
                   {"view": "coverage", "min_score": 5}):  # fmt: skip
        assert client.get(f"/api/runs/{run_id}/promos", params=params).status_code == 422


def test_filters_and_pagination_over_http(tmp_path) -> None:
    client = make_client(tmp_path)
    run_id = upload(client, [zip_file()]).json()["run_id"]
    url = f"/api/runs/{run_id}/promos"
    page1 = client.get(url, params={"view": "coverage", "limit": 3}).json()
    page2 = client.get(url, params={"view": "coverage", "limit": 3, "offset": 3}).json()
    assert [p["rank"] for p in page1["items"] + page2["items"]] == [1, 2, 3, 4, 5, 6]
    ep = client.get(url, params={"view": "coverage", "episode": [2, 3], "limit": 200}).json()
    assert ep["total"] > 0 and {p["episode_number"] for p in ep["items"]} <= {2, 3}
    alts = client.get(url, params={"view": "coverage", "include_alternatives": True, "limit": 200})
    assert alts.json()["total"] >= page1["total"]
    short = client.get(url, params={"view": "coverage", "max_duration": 25, "limit": 200}).json()
    assert all(p["duration_seconds"] <= 25 for p in short["items"])


def test_internal_errors_are_sanitized(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path)
    run_id = upload(client, [zip_file()]).json()["run_id"]
    candidate = _all(client, run_id, "coverage")[0]["candidate_id"]

    def boom(*a, **k):
        raise RuntimeError("secret detail We should check the list.")

    monkeypatch.setattr(api_module, "detail", boom)
    r = client.get(f"/api/runs/{run_id}/promos/{candidate}")
    assert r.status_code == 500 and r.json() == {
        "error": {
            "code": "internal_error",
            "message": "internal error; see server log for the type",
        }
    }


def test_cors_allows_only_configured_origins(tmp_path) -> None:
    client = make_client(tmp_path)
    ok = client.get("/api/health", headers={"Origin": ORIGIN})
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    evil = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    pre = client.options(
        "/api/runs",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 400
    assert make_client(tmp_path).app.state.config.allowed_origins == (ORIGIN,)
    default = ApiConfig()
    assert "*" not in default.allowed_origins
    assert all("localhost" in o or "127.0.0.1" in o for o in default.allowed_origins)


def test_no_secret_in_any_response_or_export(tmp_path) -> None:
    client = make_client(tmp_path)
    run_id = upload(client, [zip_file()]).json()["run_id"]
    bodies = [client.get(f"/api/runs/{run_id}{suffix}").text
              for suffix in ("", "/coverage", "/episodes", "/promos", "/export")]  # fmt: skip
    for f in (tmp_path / "store" / run_id / "exports").iterdir():
        bodies.append(f.read_text("utf-8"))
    assert all(SECRET not in b for b in bodies)


def test_store_reloads_from_disk_and_prunes(tmp_path) -> None:
    client = make_client(tmp_path, max_runs_in_memory=1, max_runs_on_disk=2)
    ids = [upload(client, [zip_file((n,))]).json()["run_id"] for n in (1, 2)]
    store = client.app.state.store
    assert ids[0] not in store._memory  # evicted from memory...
    again = client.get(f"/api/runs/{ids[0]}")
    assert again.status_code == 200 and again.json()["run_id"] == ids[0]  # ...reloaded from disk
    third = upload(client, [zip_file((3,))]).json()["run_id"]
    on_disk = sorted(p.name for p in (tmp_path / "store").iterdir())
    assert third in on_disk and len(on_disk) == 2


# --- helpers ------------------------------------------------------------------------------


def _all(client, run_id, view, alternatives=False):
    params = {"view": view, "limit": 200, "include_alternatives": alternatives}
    return client.get(f"/api/runs/{run_id}/promos", params=params).json()["items"]


def _fill_without_guard(cache, episodes):
    """fill_cache uses the real client with a mock transport; lift the guard just for it."""
    import promolens.llm_client as lc

    patched = lc.ChatCompletionsClient.__init__
    lc.ChatCompletionsClient.__init__ = _ORIGINAL_INIT
    try:
        fill_cache(cache, episodes, per_episode=2)
    finally:
        lc.ChatCompletionsClient.__init__ = patched


import promolens.llm_client as _lc  # noqa: E402

_ORIGINAL_INIT = _lc.ChatCompletionsClient.__init__
