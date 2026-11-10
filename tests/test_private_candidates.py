"""Candidate engine on the confidential pack. Skips unless FRAMECUE_TEST_ZIP is set.
Assertions use counts and ids only, and no screenplay text is printed."""

import os
import re
import time
from pathlib import Path

import pytest

from framecue.candidates import generate_pack_candidates, summarize, validate_candidates
from framecue.parser import parse_zip
from framecue.schemas import CandidateConfig

ZIP_PATH = os.environ.get("FRAMECUE_TEST_ZIP", "")
ROOT = Path(__file__).resolve().parents[1]
CFG = CandidateConfig()

pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(
        not ZIP_PATH or not Path(ZIP_PATH).is_file(),
        reason="set FRAMECUE_TEST_ZIP to the private screenplay ZIP",
    ),
]


@pytest.fixture(scope="module")
def episodes():
    return parse_zip(ZIP_PATH).raise_for_errors().episodes


@pytest.fixture(scope="module")
def run(episodes):
    started = time.perf_counter()
    results = generate_pack_candidates(episodes, CFG)
    return results, time.perf_counter() - started


def test_every_episode_has_valid_candidates(episodes, run) -> None:
    results, _ = run
    assert [r.episode_number for r in results] == [e.episode_number for e in episodes]
    for episode, result in zip(episodes, results, strict=True):
        assert result.shortlist, episode.episode_number
        assert validate_candidates(episode, result.shortlist, CFG) == []
        assert result.raw_window_count >= result.pool_size >= len(result.shortlist)


def test_structural_guarantees(episodes, run) -> None:
    results, _ = run
    stats = summarize(results, CFG)
    assert stats["duplicate_windows"] == 0
    assert stats["dialogue_boundary_conflicts"] == 0
    assert stats["episodes_with_uncovered_region"] == []
    assert stats["scene_crossing"] > 0
    shortlist = [c for r in results for c in r.shortlist]
    assert all(20_000 <= c.duration_ms <= 60_000 for c in shortlist)
    assert all(c.duration_ms == c.end_ms - c.start_ms for c in shortlist)
    for episode, result in zip(episodes, results, strict=True):
        assert any(c.end_ms == episode.duration_ms for c in result.shortlist)


def test_repeat_run_is_identical(episodes, run) -> None:
    results, _ = run
    again = generate_pack_candidates(episodes, CFG)
    assert [r.model_dump_json() for r in again] == [r.model_dump_json() for r in results]


def test_runtime_is_interactive(run) -> None:
    _, seconds = run
    assert seconds < 30, seconds


def test_no_story_names_in_tracked_sources(episodes) -> None:
    """Proper nouns from loglines and notes must not appear in code, tests or docs."""
    stop = {"Episode", "Episodes", "Season", "Part", "Picks", "First", "Continues"}
    names: set[str] = set()
    for episode in episodes:
        for text in (episode.logline or "", episode.continuity_notes or ""):
            for sentence in re.split(r"[.!?]\s+", text):
                names.update(re.findall(r"(?<=\s)[A-Z][a-z]{3,}\b", sentence))
    names -= stop
    assert len(names) >= 5  # the pattern actually finds character names
    files = [*ROOT.glob("src/**/*.py"), *ROOT.glob("tests/**/*.py"), *ROOT.glob("tests/fixtures/*")]
    files += [*ROOT.glob("docs/*.md"), ROOT / "README.md"]
    leaks = {
        f"{path.relative_to(ROOT)}:{name}"
        for path in files
        for name in names
        if re.search(rf"\b{name}\b", path.read_text(encoding="utf-8"))
    }
    assert not leaks, f"{len(leaks)} story-specific name(s) found in tracked files"
