"""Integration tests against the confidential screenplay ZIP.

Run locally with:  FRAMECUE_TEST_ZIP=/path/to/pack.zip pytest -m private
They skip when the variable is unset (e.g. in CI). Assertions report counts and IDs only;
no screenplay text is printed or stored.
"""

import os
import re
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from framecue.parser import parse_zip
from framecue.schemas import EventType, ParseResult, Severity

ZIP_PATH = os.environ.get("FRAMECUE_TEST_ZIP", "")

pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(
        not ZIP_PATH or not Path(ZIP_PATH).is_file(),
        reason="set FRAMECUE_TEST_ZIP to the private screenplay ZIP",
    ),
]

# Independent of the parser's regexes, so a parser bug cannot hide itself.
LABEL_LINE = re.compile(r"^\*\*.+:\*\*$")
TIMING_LINE = re.compile(r"^Start: \((\d\d):(\d\d)\.(\d\d)\) \| End: \((\d\d):(\d\d)\.(\d\d)\)$")


def to_ms(minutes: str, seconds: str, centis: str) -> int:
    return int(minutes) * 60_000 + int(seconds) * 1_000 + int(centis) * 10


@pytest.fixture(scope="module")
def result() -> ParseResult:
    return parse_zip(ZIP_PATH)


@pytest.fixture(scope="module")
def raw_files() -> dict[str, list[str]]:
    with zipfile.ZipFile(ZIP_PATH) as zf:
        return {
            info.filename: zf.read(info).decode("utf-8").split("\n")
            for info in zf.infolist()
            if info.filename.endswith(".txt")
        }


def test_pack_parses_without_errors(result: ParseResult) -> None:
    assert result.ok, [str(issue) for issue in result.errors]


def test_counts_match_phase0_audit(result: ParseResult) -> None:
    assert result.counts() == {
        "episodes": 50,
        "scenes": 100,
        "events": 3727,
        "events_CAMERA": 1754,
        "events_DIALOGUE": 1487,
        "events_ACTION": 372,
        "events_SOUND_FX": 114,
    }


def test_episode_numbers_unique_and_contiguous(result: ParseResult) -> None:
    assert [e.episode_number for e in result.episodes] == list(range(1, 51))


def test_every_raw_block_is_accounted_for(result: ParseResult, raw_files) -> None:
    by_source = {episode.source_name: episode for episode in result.episodes}
    assert set(by_source) == set(raw_files)
    for name, lines in raw_files.items():
        stripped = [line.strip() for line in lines]
        assert sum(map(bool, map(LABEL_LINE.match, stripped))) == len(by_source[name].events)
        assert sum(map(bool, map(TIMING_LINE.match, stripped))) == len(by_source[name].events)


def test_events_match_source_lines_exactly(result: ParseResult, raw_files) -> None:
    for episode in result.episodes:
        lines = raw_files[episode.source_name]
        for event in episode.events:
            assert lines[event.source_line - 1].strip() == f"**{event.label}:**"
            timing = TIMING_LINE.match(lines[event.source_line].strip())
            assert timing is not None
            assert event.start_ms == to_ms(*timing.groups()[:3])
            assert event.end_ms == to_ms(*timing.groups()[3:])
            text_lines = event.text.split("\n")
            start = event.source_line + 1
            assert [line.strip() for line in lines[start : start + len(text_lines)]] == text_lines


def test_no_content_lost(result: ParseResult, raw_files) -> None:
    """Every non-blank line is a header field, marker, scene line, event line or preserved."""
    for episode in result.episodes:
        non_blank = sum(1 for line in raw_files[episode.source_name] if line.strip())
        header = 3 + len(episode.extra_metadata)
        scene_lines = 5 * len(episode.scenes)  # SCENE, heading, start, end, actions marker
        event_lines = sum(2 + len(event.text.split("\n")) for event in episode.events)
        unparsed = len(episode.unparsed_lines) + sum(len(s.unparsed_lines) for s in episode.scenes)
        assert non_blank == header + 2 + scene_lines + event_lines + unparsed, episode.source_name


def test_ids_unique_and_stable(result: ParseResult) -> None:
    ids = [event.event_id for episode in result.episodes for event in episode.events]
    assert len(ids) == len(set(ids)) == 3727
    assert ids == [ev.event_id for ep in parse_zip(ZIP_PATH).episodes for ev in ep.events]


def test_scene_boundaries_are_contiguous(result: ParseResult) -> None:
    for episode in result.episodes:
        assert episode.scenes[0].start_ms == 0
        for previous, scene in zip(episode.scenes, episode.scenes[1:], strict=False):
            assert scene.start_ms == previous.end_ms
            assert scene.scene_number == previous.scene_number + 1


def test_source_order_and_timeline(result: ParseResult) -> None:
    for episode in result.episodes:
        events = episode.events
        assert [e.source_index for e in events] == list(range(len(events)))
        timeline = episode.timeline()
        assert sorted(e.event_id for e in timeline) == sorted(e.event_id for e in events)
        keys = [(e.start_ms, e.source_index) for e in timeline]
        assert keys == sorted(keys)


def test_known_source_anomalies_are_reported(result: ParseResult) -> None:
    """Expected from the Phase 0 audit; see docs/PLAN.md section 4."""
    events = {ev.event_id: ev for ep in result.episodes for ev in ep.events}
    assert result.issue_counts() == {
        "INFO:same_type_overlap": 20,
        "WARNING:event_outside_scene": 1,
        "WARNING:source_order_inversion": 8,
    }
    overlap_types = Counter(
        events[i.event_id].event_type for i in result.issues if i.code == "same_type_overlap"
    )
    assert overlap_types == {
        EventType.DIALOGUE: 10,
        EventType.CAMERA: 5,
        EventType.ACTION: 4,
        EventType.SOUND_FX: 1,
    }
    spill = next(i for i in result.issues if i.code == "event_outside_scene")
    assert events[spill.event_id].event_type is EventType.SOUND_FX
    assert "300 ms" in spill.message
    assert all(i.severity is not Severity.ERROR for i in result.issues)


def test_diagnostics_contain_no_script_text(result: ParseResult) -> None:
    texts = {ev.text for ep in result.episodes for ev in ep.events if len(ev.text) >= 12}
    rendered = "\n".join(str(issue) for issue in result.issues)
    assert not any(text in rendered for text in texts)


def test_repeat_parse_is_identical(result: ParseResult) -> None:
    assert parse_zip(ZIP_PATH).model_dump_json() == result.model_dump_json()
