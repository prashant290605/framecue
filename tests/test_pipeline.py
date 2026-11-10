"""Offline orchestration, cache merging, ranking views, exports and the CLI (synthetic data)."""

import csv
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest

from pipeline_fakes import episode_text, fill_cache, groq_settings, pack_bytes, write_pack
from promolens import cli
from promolens.eval_driver import coverage_first
from promolens.evaluator import EvaluationCache, EvaluationConfig, Evaluator, cache_key
from promolens.exports import write_exports
from promolens.parser import parse_path, parse_zip_bytes
from promolens.pipeline import NoEpisodesError, analyze_path, coverage, run_summary
from promolens.ranking import PromoFilter, near_duplicate, page, suppress
from promolens.results import AIStatus
from promolens.schemas import CandidateConfig

NUMBERS = [1, 2, 3]


@pytest.fixture
def pack(tmp_path) -> Path:
    return write_pack(tmp_path / "in", NUMBERS)


def episodes_of(path: Path):
    return list(parse_path(path).episodes)


def run_of(path: Path, cache_dir: Path | None):
    return analyze_path(path, cache_dir=cache_dir)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP request in these tests is a failure (no live inference)."""

    def refuse(self, request):
        raise AssertionError(f"unexpected network call to {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


# --- orchestration -----------------------------------------------------------------------


def test_offline_pack_keeps_every_episode_and_candidate(pack, tmp_path) -> None:
    run = run_of(pack, tmp_path / "empty-cache")
    assert sorted(run.episodes) == NUMBERS
    assert all(run.candidates[n] for n in NUMBERS)
    cov = coverage(run)
    assert cov.total_candidates == len(run.by_id) and cov.ai_evaluated == 0
    assert cov.episodes_without_ai == tuple(NUMBERS) and not cov.complete
    view = page(run, "coverage", PromoFilter(include_alternatives=True), limit=10_000)
    assert view.total == len(run.by_id)  # nothing dropped
    assert {p.episode_number for p in view.items} == set(NUMBERS)
    assert page(run, "ai").total == 0 and run_summary(run).live_ai_calls == 0


def test_new_inputs_txt_zip_and_folder(tmp_path) -> None:
    txt = tmp_path / "episode-07.txt"
    txt.write_text(episode_text(7), encoding="utf-8")
    one = analyze_path(txt, cache_dir=None)
    assert list(one.episodes) == [7] and one.input.kind == "txt"
    folder = tmp_path / "folder"
    folder.mkdir()
    for n in (4, 5):
        (folder / f"e{n}.txt").write_bytes(episode_text(n).encode("utf-8"))
    many = analyze_path(folder, cache_dir=None)
    assert sorted(many.episodes) == [4, 5] and many.input.kind == "directory"
    with pytest.raises(NoEpisodesError) as info:
        analyze_path(_write(tmp_path / "bad.txt", b"\xff\xfe not utf-8"), cache_dir=None)
    assert "invalid_utf8" in info.value.codes


def test_unsafe_and_oversized_archives_are_rejected(monkeypatch) -> None:
    traversal = pack_bytes([1], extra={"../evil.txt": episode_text(9).encode()})
    result = parse_zip_bytes(traversal, "x.zip")
    assert [e.episode_number for e in result.episodes] == [1]
    assert "unsafe_archive_entry" in {i.code for i in result.issues}
    monkeypatch.setattr("promolens.parser.MAX_ARCHIVE_TOTAL_BYTES", 10_000)
    capped = parse_zip_bytes(pack_bytes([1, 2, 3]), "big.zip")
    assert "archive_too_large" in {i.code for i in capped.issues}
    assert parse_zip_bytes(b"PK\x03\x04garbage", "broken.zip").issues[0].code == (
        "unreadable_archive"
    )


# --- AI cache merging and audit ----------------------------------------------------------


def test_cached_ai_evaluations_are_merged_and_audited(pack, tmp_path) -> None:
    cache = tmp_path / "cache"
    evaluated = fill_cache(cache, episodes_of(pack)[:2], per_episode=2)
    run = run_of(pack, cache)
    assert set(run.ai) == set(evaluated) and len(evaluated) == 4
    assert all(run.status_of(c) is AIStatus.AI_EVALUATED for c in evaluated)
    cov = coverage(run)
    assert cov.ai_evaluated == 4 and cov.episodes_with_ai == 2 and cov.episodes_without_ai == (3,)
    assert cov.cache.entries_scanned == 4 and cov.cache.used == 4
    assert all(run.ai[c].provenance.from_cache for c in evaluated)


def test_cache_mismatches_are_excluded_and_counted(pack, tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    eps = episodes_of(pack)
    fill_cache(cache_dir, eps[:1], per_episode=1)  # current profile, used
    fill_cache(
        cache_dir, eps[1:2], per_episode=1, settings=groq_settings(reasoning_effort="medium")
    )
    cache = EvaluationCache(cache_dir)
    stale_key = "ab" + "0" * 62
    cache.put(stale_key, {"assessment": {"candidate_id": "C001-0000000-0030000"},
                          "provenance": {"prompt_version": "promo-eval-v1"}})  # fmt: skip
    foreign_key = "cd" + "0" * 62
    entry = cache.get(cache.keys()[0])
    entry["assessment"]["candidate_id"] = "C099-0000000-0030000"
    cache.put(foreign_key, entry)
    # changed input: same episode number, different text -> different checksum -> no reuse
    changed = tmp_path / "changed"
    changed.mkdir()
    (changed / "e1.txt").write_text(episode_text(1, line="A different line now."), "utf-8")
    run = run_of(pack, cache_dir)
    audit = coverage(run).cache
    assert audit.used == 1 and len(run.ai) == 1
    assert audit.stale_prompt_version == 1 and audit.stale_prompt_versions_seen == (
        "promo-eval-v1",
    )
    assert audit.other_profile_or_input == 1  # the medium-reasoning entry
    assert audit.not_in_this_input == 1
    rerun = analyze_path(changed, cache_dir=cache_dir)
    assert rerun.ai == {}


def test_tampered_cache_entry_is_rejected_not_used(pack, tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    [cid] = fill_cache(cache_dir, episodes_of(pack)[:1], per_episode=1)
    cache = EvaluationCache(cache_dir)
    key = cache.keys()[0]
    entry = cache.get(key)
    entry["assessment"]["hook_event_ids"] = ["E001-S01-9999"]
    (cache_dir / key[:2] / f"{key}.json").write_text(json.dumps(entry), "utf-8")
    run = run_of(pack, cache_dir)
    assert cid not in run.ai and coverage(run).cache.invalid_on_revalidation == (key,)


def test_genuine_failures_are_reported_but_budget_stops_are_not(pack, tmp_path) -> None:
    eps = episodes_of(pack)
    cache_dir = tmp_path / "cache"
    first = list(run_of(pack, None).candidates[1])[0]
    bad = lambda request: httpx.Response(200, json={  # noqa: E731
        "choices": [{"finish_reason": "stop", "message": {"content": '{"evaluations": []}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})  # fmt: skip
    from promolens.llm_client import ChatCompletionsClient

    settings = groq_settings()
    client = ChatCompletionsClient(settings, httpx.MockTransport(bad), lambda _: None)
    ev = Evaluator(
        client, settings, EvaluationConfig(), CandidateConfig(), EvaluationCache(cache_dir)
    )
    ev.evaluate_run([(eps[0], [first])])  # model never returns the item -> genuine failure
    stopped = Evaluator(client, settings, EvaluationConfig(max_run_tokens=0), CandidateConfig(),
                        EvaluationCache(cache_dir))  # fmt: skip
    second = list(run_of(pack, None).candidates[2])[0]
    stopped.evaluate_run([(eps[1], [first]), (eps[1], [second])])
    run = run_of(pack, cache_dir)
    assert run.status_of(first.candidate_id) is AIStatus.FAILED
    assert run.failed[first.candidate_id].startswith("validation_failed_after_")
    assert run.status_of(second.candidate_id) is AIStatus.NOT_EVALUATED
    assert coverage(run).failed == 1
    assert first.candidate_id not in {p.candidate_id for p in page(run, "ai").items}


# --- ranking ------------------------------------------------------------------------------


@pytest.fixture
def scored(pack, tmp_path):
    eps = episodes_of(pack)
    base = run_of(pack, None)
    c = {n: [x.candidate_id for x in base.candidates[n]] for n in NUMBERS}
    scores = {
        c[1][0]: (4, 4, 4, 4),  # 80
        c[1][1]: (3, 3, 3, 3),  # 60
        c[2][0]: (2, 5, 3, 3),  # tie at 66: cliffhanger 5 wins
        c[2][1]: (5, 2, 3, 3),  # 66
    }
    fill_cache(tmp_path / "cache", eps[:2], per_episode=2, scores=scores)
    return run_of(pack, tmp_path / "cache"), c


def test_ai_view_contains_only_ai_evaluated_in_score_order_with_tie_breakers(scored) -> None:
    run, c = scored
    view = page(run, "ai", PromoFilter(include_alternatives=True), limit=100)
    assert {p.candidate_id for p in view.items} == set(run.ai)
    assert all(p.evaluation_status is AIStatus.AI_EVALUATED for p in view.items)
    assert all(p.score.system == "ai_weighted_v1" and p.score.scale_max == 100 for p in view.items)
    ranked = [p for p in view.items if p.rank is not None]
    values = [p.score.value for p in ranked]
    assert values == sorted(values, reverse=True)
    order = [p.candidate_id for p in view.items]
    assert run.ai[c[2][0]].weighted_score == run.ai[c[2][1]].weighted_score == 66.0
    assert order.index(c[2][0]) < order.index(c[2][1])  # tie: higher cliffhanger first
    assert view.provisional and "of" in view.provisional_reason


def test_coverage_view_is_baseline_only_even_for_ai_evaluated(scored) -> None:
    run, c = scored
    view = page(run, "coverage", PromoFilter(include_alternatives=True), limit=1000)
    assert view.total == len(run.by_id) and not view.provisional
    for p in view.items:
        assert p.score.system == "heuristic_priority_v1" and p.score.scale_max == 1
        assert p.hook_explanation is None and p.why_it_works is None and p.hook_event_ids == ()
        assert p.score.value == run.by_id[p.candidate_id].heuristic.priority
        assert "not AI-reviewed" in p.label
    reviewed = [p for p in view.items if p.candidate_id in run.ai]
    assert reviewed and all(p.ai_score_available for p in reviewed)  # flagged, never mixed in
    priorities = [p.score.value for p in view.items if p.rank is not None]
    assert priorities == sorted(priorities, reverse=True)


def test_near_duplicate_rules_and_suppression(pack) -> None:
    run = run_of(pack, None)
    cands = sorted(run.by_id.values(), key=lambda x: (x.episode_number, x.start_ms))
    a = cands[0]
    same = [b for b in cands if b.episode_number == a.episode_number and b is not a]
    other = next(b for b in cands if b.episode_number != a.episode_number)
    assert near_duplicate(a, a) and not near_duplicate(a, other)
    for b in same:
        overlap = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
        contained = overlap / min(a.duration_ms, b.duration_ms) >= 0.8
        assert near_duplicate(a, b) == (contained or _iou(a, b) >= 0.5)
    entries = suppress(cands)
    primaries = [e for e in entries if e.rank is not None]
    assert [e.rank for e in primaries] == list(range(1, len(primaries) + 1))
    for e in entries:
        if e.alternative_of:
            assert e.rank is None
            assert near_duplicate(run.by_id[e.alternative_of], run.by_id[e.candidate_id])
    primary_ids = [run.by_id[e.candidate_id] for e in primaries]
    for i, x in enumerate(primary_ids):
        assert not any(near_duplicate(x, y) for y in primary_ids[i + 1 :])


def test_filters_and_pagination_keep_original_ranks(scored) -> None:
    run, c = scored
    everything = page(run, "coverage", limit=1000).items
    ep2 = page(run, "coverage", PromoFilter(episodes=frozenset({2})), limit=1000).items
    assert ep2 and all(p.episode_number == 2 for p in ep2)
    full_rank = {p.candidate_id: p.rank for p in everything}
    assert all(p.rank == full_rank[p.candidate_id] for p in ep2)
    first, second = page(run, "coverage", limit=2), page(run, "coverage", offset=2, limit=2)
    assert [p.rank for p in first.items] == [1, 2] and [p.rank for p in second.items] == [3, 4]
    assert first.total == second.total == len(everything)
    short = page(run, "coverage", PromoFilter(max_duration_s=25), limit=1000).items
    assert all(p.duration_seconds <= 25 for p in short)
    strong = page(run, "ai", PromoFilter(min_score=70), limit=100).items
    assert strong and all(p.score.value >= 70 for p in strong)
    pending = PromoFilter(statuses=frozenset({AIStatus.NOT_EVALUATED}))
    assert all(
        p.candidate_id not in run.ai for p in page(run, "coverage", pending, limit=999).items
    )
    none_warned = page(run, "ai", PromoFilter(warnings_any=frozenset({"weak_ending"})))
    assert none_warned.total == 0  # the fake model returns no warnings


def test_segments_and_timestamps_are_exact(scored) -> None:
    run, _ = scored
    p = page(run, "ai", limit=1).items[0]
    cand = run.by_id[p.candidate_id]
    [seg] = p.segments
    assert (seg.start_ms, seg.end_ms) == (cand.start_ms, cand.end_ms)
    assert seg.duration_ms == cand.duration_ms
    minutes, rest = divmod(cand.start_ms, 60_000)
    assert seg.start_timestamp == f"{minutes:02d}:{rest // 1000:02d}.{rest % 1000 // 10:02d}"
    assert p.duration_seconds == round(cand.duration_ms / 1000, 2)


# --- exports ------------------------------------------------------------------------------


def test_exports_are_complete_exact_and_deterministic(scored, tmp_path) -> None:
    run, _ = scored
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    manifest = write_exports(run, out1, raw_dir=None)
    write_exports(run, out2, raw_dir=None)
    names = {f.name for f in manifest.files}
    assert names == {
        "episodes.json", "candidates.jsonl", "ai_evaluations.jsonl", "rankings_ai.json",
        "rankings_coverage.json", "ai_recommendations.csv", "coverage_candidates.csv",
    }  # fmt: skip
    for f in manifest.files:
        data = (out1 / f.name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f.sha256 and len(data) == f.bytes
        assert data == (out2 / f.name).read_bytes()  # deterministic
    lines = (out1 / "candidates.jsonl").read_text("utf-8").splitlines()
    records = [json.loads(x) for x in lines]
    assert len(records) == len(run.by_id) and {r["episode_number"] for r in records} == set(NUMBERS)
    assert all(r["segments"][0]["start_timestamp"] and r["start_ms"] >= 0 for r in records)
    no_ai = [r for r in records if r["candidate_id"] not in run.ai]
    assert all(r["ai"] is None and r["evaluation_status"] == "not_evaluated" for r in no_ai)
    rows = list(csv.DictReader(io.StringIO((out1 / "ai_recommendations.csv").read_text("utf-8"))))
    assert len(rows) == len(run.ai)
    top = rows[0]
    e = run.ai[top["candidate_id"]]
    assert float(top["score"]) == e.weighted_score and top["score_system"] == "ai_weighted_v1"
    assert top["hook_explanation"] == e.hook_explanation
    assert top["hook_event_ids"] == ";".join(e.hook_event_ids)
    assert int(top["start_ms"]) == e.start_ms and top["rank"] == "1"
    cov_rows = list(
        csv.DictReader(io.StringIO((out1 / "coverage_candidates.csv").read_text("utf-8")))
    )
    assert len(cov_rows) == len(run.by_id)
    assert all(r["hook_score"] == "" and r["hook_explanation"] == "" for r in cov_rows)
    m = json.loads((out1 / "manifest.json").read_text("utf-8"))
    assert m["run"]["coverage"]["ai_evaluated"] == len(run.ai)
    assert "Not available" in m["raw_model_outputs"]
    assert any("INCOMPLETE" in n for n in m["notes"])


def test_raw_model_output_is_captured_unmodified_and_exported(pack, tmp_path) -> None:
    eps = episodes_of(pack)
    raw_dir = tmp_path / "raw"
    cache_dir = tmp_path / "cache"
    from llm_fakes import FakeOpenRouter
    from promolens.llm_client import ChatCompletionsClient

    cands = list(run_of(pack, None).candidates[1])[:1]
    fake = FakeOpenRouter(cands)
    settings = groq_settings()
    client = ChatCompletionsClient(settings, httpx.MockTransport(fake), lambda _: None)
    Evaluator(client, settings, EvaluationConfig(), CandidateConfig(), EvaluationCache(cache_dir),
              raw_log=raw_dir / "groq.jsonl").evaluate_run([(eps[0], cands)])  # fmt: skip
    [record] = [json.loads(x) for x in (raw_dir / "groq.jsonl").read_text("utf-8").splitlines()]
    assert record["candidate_ids"] == [cands[0].candidate_id]
    assert json.loads(record["content"])["evaluations"][0]["candidate_id"] == cands[0].candidate_id
    run = run_of(pack, cache_dir)
    manifest = write_exports(run, tmp_path / "out", raw_dir=raw_dir)
    assert "raw_model_outputs.jsonl" in {f.name for f in manifest.files}
    from llm_fakes import TEST_KEY

    assert all(TEST_KEY not in f.read_text("utf-8") for f in (tmp_path / "out").iterdir())


# --- CLI ----------------------------------------------------------------------------------


def test_cli_analyze_offline_with_summary(pack, tmp_path, capsys, monkeypatch) -> None:
    from promolens import llm_client

    def no_client(*a, **k):
        raise AssertionError("analyze must not create a model client")

    monkeypatch.setattr(llm_client.ChatCompletionsClient, "__init__", no_client)
    monkeypatch.setenv("PROMOLENS_LLM_API_KEY", "sk-should-never-appear-123")
    out = tmp_path / "out"
    code = cli.main(["analyze", str(pack), "--out", str(out), "--cache-dir",
                     str(tmp_path / "cache"), "--raw-dir", str(tmp_path / "raw")])  # fmt: skip
    printed = capsys.readouterr().out
    assert code == 0 and (out / "manifest.json").is_file()
    assert "no model API calls" in printed and "AI coverage: 0/" in printed
    assert "We should check the list" not in printed  # no screenplay text on the console
    assert "sk-should-never-appear" not in printed + "".join(
        p.read_text("utf-8") for p in out.iterdir()
    )
    assert cli.main(["analyze", str(tmp_path / "missing.zip")]) == 2
    assert cli.main(["analyze", str(_write(tmp_path / "x.pdf", b"%PDF"))]) == 2
    assert cli.main(["analyze", str(_write(tmp_path / "e.txt", b"no episode here"))]) == 3


def test_cli_repeat_runs_produce_identical_outputs(pack, tmp_path) -> None:
    for name in ("a", "b"):
        assert cli.main(["analyze", str(pack), "--out", str(tmp_path / name), "--no-cache",
                         "--raw-dir", str(tmp_path / "raw")]) == 0  # fmt: skip
    for f in (tmp_path / "a").iterdir():
        if f.name != "manifest.json":
            assert f.read_bytes() == (tmp_path / "b" / f.name).read_bytes(), f.name


# --- scheduling -----------------------------------------------------------------------------


def test_coverage_first_schedule_prefers_uncovered_episodes(pack) -> None:
    run = run_of(pack, None)
    plan = [(run.episodes[n], run.candidates[n]) for n in NUMBERS]
    done = {run.candidates[1][0].candidate_id}
    picks = coverage_first(plan, lambda e, c: c.candidate_id in done, levels=2)
    first_level = [(e.episode_number, cs[0].candidate_id) for e, cs in picks[:2]]
    assert first_level == [(2, run.candidates[2][0].candidate_id),
                           (3, run.candidates[3][0].candidate_id)]  # fmt: skip
    assert [e.episode_number for e, _ in picks[2:]] == [1, 2, 3]
    assert all(len(cs) == 1 and cs[0].candidate_id not in done for _, cs in picks)
    assert picks == coverage_first(plan, lambda e, c: c.candidate_id in done, levels=2)


# --- helpers --------------------------------------------------------------------------------


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _iou(a, b) -> float:
    inter = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    return inter / (a.duration_ms + b.duration_ms - inter)


def test_cache_key_matches_between_evaluator_and_pipeline(pack) -> None:
    run = run_of(pack, None)
    c = run.candidates[1][0]
    from promolens.pipeline import default_ai_profile

    assert cache_key(run.episodes[1], c, default_ai_profile(), CandidateConfig()) == cache_key(
        run.episodes[1], c, groq_settings(), CandidateConfig()
    )


def test_cli_evaluate_passes_arguments_to_the_driver_as_a_dry_run(
    pack, tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here; the dry run must not need a key
    code = cli.main(["evaluate", "--zip", str(pack), "--schedule", "coverage-first",
                     "--cache-dir", str(tmp_path / "cache")])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "schedule=coverage-first levels=1: 3 candidates selected" in out
    assert "dry run: no model API calls made" in out
