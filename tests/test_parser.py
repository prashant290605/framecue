import pydantic
import pytest

from promolens.parser import parse_episode_text, parse_path
from promolens.schemas import (
    Episode,
    EventType,
    ParseResult,
    ScreenplayParseError,
    Severity,
)
from synthetic import FIXTURES, episode, event, scene, simple_episode

FIXTURE = FIXTURES / "synthetic_episode_01.txt"


def codes(result: ParseResult, severity: Severity | None = None) -> list[str]:
    return [i.code for i in result.issues if severity is None or i.severity is severity]


def only_episode(text: str) -> Episode:
    result = parse_episode_text(text, "synthetic.txt").raise_for_errors()
    assert len(result.episodes) == 1
    return result.episodes[0]


# --- normal parsing ---------------------------------------------------------


def test_fixture_parses_without_issues() -> None:
    result = parse_path(FIXTURE)
    assert result.ok and result.issues == ()
    ep = result.episodes[0]
    assert ep.episode_number == 1
    assert ep.logline.startswith("A lighthouse keeper")
    assert ep.continuity_notes.startswith("Synthetic fixture")
    assert [s.scene_number for s in ep.scenes] == [1, 2]
    assert [s.heading for s in ep.scenes] == [
        "INT. LIGHTHOUSE KITCHEN – NIGHT",
        "EXT. CLIFF PATH – CONTINUOUS",
    ]
    assert (ep.scenes[1].start_ms, ep.scenes[1].end_ms) == (12_500, 25_030)
    assert ep.duration_ms == 25_030
    assert result.counts() == {
        "episodes": 1,
        "scenes": 2,
        "events": 11,
        "events_CAMERA": 5,
        "events_DIALOGUE": 3,
        "events_ACTION": 2,
        "events_SOUND_FX": 1,
    }


def test_event_fields_preserve_source() -> None:
    ep = parse_path(FIXTURE).episodes[0]
    courier = ep.events[5]
    assert courier.event_id == "E001-S01-0005"
    assert courier.event_type is EventType.DIALOGUE
    assert courier.label == "COURIER (O.S.) (Muffled, Urgent)"
    assert courier.speaker == "COURIER"
    assert courier.annotations == ("O.S.", "Muffled, Urgent")
    assert courier.text == '"Sign nothing. Burn it after reading."'
    assert (courier.start_ms, courier.end_ms, courier.duration_ms) == (4_500, 6_100, 1_600)
    assert courier.source_line == 34
    sfx = ep.events[2]
    assert sfx.event_type is EventType.SOUND_FX and sfx.label == "SOUND FX"
    assert sfx.speaker is None and sfx.annotations == ()


def test_event_ids_are_unique_and_follow_source_order() -> None:
    ep = parse_path(FIXTURE).episodes[0]
    assert [e.source_index for e in ep.events] == list(range(11))
    assert [e.event_id[-4:] for e in ep.events] == [f"{i:04d}" for i in range(11)]
    assert ep.events[7].event_id == "E001-S02-0007"


def test_unicode_text_is_preserved() -> None:
    ep = parse_path(FIXTURE).episodes[0]
    assert "café-brown" in ep.events[-1].text
    assert "—" in ep.events[-2].text


def test_multi_line_event_text_is_joined_with_newlines() -> None:
    body = event("ACTION", "00:00.00", "00:20.00", "First line.\nSecond line.")
    ep = only_episode(episode(1, [scene(1, "00:00.00", "00:20.00", [body])]))
    assert ep.events[0].text == "First line.\nSecond line."


# --- headers, case and encoding --------------------------------------------


def test_uppercase_structural_headers_parse_identically() -> None:
    lower = only_episode(simple_episode(upper=False))
    upper = only_episode(simple_episode(upper=True))
    assert upper.scenes == lower.scenes


def test_crlf_and_bom_parse_identically(tmp_path) -> None:
    text = simple_episode()
    lf_file = tmp_path / "lf.txt"
    crlf_file = tmp_path / "crlf.txt"
    lf_file.write_bytes(text.encode("utf-8"))
    crlf_file.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    lf, crlf = parse_path(lf_file), parse_path(crlf_file)
    assert lf.ok and crlf.ok
    assert lf.episodes[0].scenes == crlf.episodes[0].scenes


def test_invalid_utf8_is_an_error(tmp_path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_bytes(simple_episode().encode("utf-8") + b"\xff\xfe")
    result = parse_path(bad)
    assert not result.ok and codes(result) == ["invalid_utf8"]
    assert result.episodes == ()


def test_unknown_header_field_is_kept() -> None:
    text = simple_episode().replace("CONTINUITY NOTES", "SEASON: 1\nCONTINUITY NOTES", 1)
    result = parse_episode_text(text)
    assert result.ok and codes(result) == ["unknown_header_field"]
    assert result.episodes[0].extra_metadata == {"SEASON": "1"}


def test_missing_logline_is_a_warning() -> None:
    result = parse_episode_text(simple_episode().replace("A synthetic logline.", ""))
    assert result.ok and codes(result) == ["missing_header_field"]
    assert result.episodes[0].logline is None


# --- concurrency and ordering ------------------------------------------------


def test_overlapping_events_are_all_preserved() -> None:
    events = [
        event("CAMERA", "00:00.00", "00:12.00"),
        event("ACTION", "00:00.50", "00:19.00"),
        event("A (Loud)", "00:01.00", "00:03.00", '"One."'),
        event("B (Soft)", "00:02.50", "00:04.00", '"Two."'),
        event("CAMERA", "00:11.00", "00:20.00"),
    ]
    result = parse_episode_text(episode(1, [scene(1, "00:00.00", "00:20.00", events)]))
    assert result.ok
    assert len(result.episodes[0].events) == 5
    assert {i.code for i in result.issues} == {"same_type_overlap"}
    assert {i.severity for i in result.issues} == {Severity.INFO}
    assert [i.event_id for i in result.issues] == ["E001-S01-0003", "E001-S01-0004"]
    assert "DIALOGUE event by 500 ms" in result.issues[0].message
    assert "CAMERA event by 1000 ms" in result.issues[1].message


def test_out_of_order_events_keep_source_order_and_sort_timeline() -> None:
    events = [
        event("CAMERA", "00:00.00", "00:10.00"),
        event("ACTION", "00:05.00", "00:08.00"),
        event("SOUND FX", "00:04.00", "00:05.00", "A synthetic bang."),
        event("CAMERA", "00:10.00", "00:20.00"),
    ]
    result = parse_episode_text(episode(1, [scene(1, "00:00.00", "00:20.00", events)]))
    assert result.ok and codes(result, Severity.WARNING) == ["source_order_inversion"]
    ep = result.episodes[0]
    assert [e.start_ms for e in ep.events] == [0, 5_000, 4_000, 10_000]
    assert [e.start_ms for e in ep.timeline()] == [0, 4_000, 5_000, 10_000]
    assert [e.source_index for e in ep.timeline()] == [0, 2, 1, 3]


def test_timeline_ties_break_by_source_index() -> None:
    events = [
        event("ACTION", "00:00.00", "00:20.00"),
        event("CAMERA", "00:00.00", "00:20.00"),
    ]
    ep = only_episode(episode(1, [scene(1, "00:00.00", "00:20.00", events)]))
    assert [e.event_type for e in ep.timeline()] == [EventType.ACTION, EventType.CAMERA]


# --- scene boundaries --------------------------------------------------------


def test_event_past_scene_end_keeps_original_timing() -> None:
    first = scene(1, "00:00.00", "00:10.00", [event("SOUND FX", "00:09.00", "00:10.30", "Boom.")])
    second = scene(2, "00:10.00", "00:20.00", [event("CAMERA", "00:10.00", "00:20.00")])
    result = parse_episode_text(episode(1, [first, second]))
    assert result.ok and codes(result) == ["event_outside_scene"]
    assert "ends 300 ms after" in result.issues[0].message
    assert result.episodes[0].events[0].end_ms == 10_300


def test_scene_gap_is_warning_and_overlap_is_error() -> None:
    a = scene(1, "00:00.00", "00:10.00", [event("CAMERA", "00:00.00", "00:10.00")])
    gap = scene(2, "00:11.00", "00:20.00", [event("CAMERA", "00:11.00", "00:20.00")])
    overlap = scene(2, "00:09.00", "00:20.00", [event("CAMERA", "00:09.00", "00:20.00")])
    assert codes(parse_episode_text(episode(1, [a, gap]))) == ["scene_gap"]
    result = parse_episode_text(episode(1, [a, overlap]))
    assert not result.ok and "scene_overlap" in codes(result, Severity.ERROR)


def test_episode_not_starting_at_zero_is_warning() -> None:
    late = scene(1, "00:01.00", "00:20.00", [event("CAMERA", "00:01.00", "00:20.00")])
    assert codes(parse_episode_text(episode(1, [late]))) == ["episode_not_starting_at_zero"]


# --- malformed input is rejected, never silently accepted ---------------------


def _one_scene(*events: str) -> str:
    return episode(1, [scene(1, "00:00.00", "00:20.00", list(events))])


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (_one_scene(event("CAMERA", "00:00.0", "00:05.00")), "malformed_timestamp"),
        (_one_scene(event("CAMERA", "00:61.00", "01:05.00")), "malformed_timestamp"),
        (_one_scene("**CAMERA:**\nStart: (00:00.00)\nNo end time.\n"), "malformed_timing_line"),
        (_one_scene("**CAMERA:**\nA shot with no timing.\n"), "event_text_before_timing"),
        (_one_scene("**CAMERA:**\n\n**ACTION:**\nStart: (00:00.00) | End: (00:01.00)\nX.\n"),
         "event_missing_timing"),
        (_one_scene("**CAMERA:**\nStart: (00:00.00) | End: (00:01.00)\n"), "event_missing_text"),
        (_one_scene(event("CAMERA", "00:05.00", "00:04.00")), "reversed_event_duration"),
        (_one_scene("Start: (00:00.00) | End: (00:01.00)\n"), "unexpected_timing_line"),
        (_one_scene("**CAMERA**\nStart: (00:00.00) | End: (00:01.00)\nX.\n"),
         "malformed_event_label"),
        (episode(1, [scene(1, "00:10.00", "00:05.00", [])]), "invalid_scene_duration"),
        (episode(1, [scene(1, "00:00.00", "00:20.00", [], heading="")]), "missing_scene_field"),
        (episode(1, [scene(1, "0:00", "00:20.00", [])]), "malformed_timestamp"),
        (episode(1, [scene(1, "00:00.00", "00:10.00", []), scene(1, "00:10.00", "00:20.00", [])]),
         "duplicate_scene_number"),
        (simple_episode().replace("SCENE 01\n", "", 1), "scene_field_outside_scene"),
        (simple_episode().replace("--- SCRIPT END ---", ""), "missing_script_marker"),
        (simple_episode().replace("Episode 1", "Episode one"), "invalid_episode_number"),
        (simple_episode().replace("EPISODE NUMBER: Episode 1\n", ""), "missing_episode_number"),
        (episode(1, []), "no_scenes"),
    ],
)  # fmt: skip
def test_malformed_input_is_an_error(text: str, code: str) -> None:
    result = parse_episode_text(text, "bad.txt")
    assert not result.ok
    assert code in codes(result, Severity.ERROR)
    assert result.episodes == ()
    with pytest.raises(ScreenplayParseError, match=code):
        result.raise_for_errors()


def test_error_reports_location() -> None:
    result = parse_episode_text(_one_scene(event("CAMERA", "00:05.00", "00:04.00")), "x.txt")
    (issue,) = result.errors
    assert (issue.source, issue.line, issue.episode_number, issue.scene_number) == (
        "x.txt",
        14,
        1,
        1,
    )
    assert issue.event_id == "E001-S01-0000"
    assert str(issue).startswith("ERROR reversed_event_duration at x.txt:14 [E001-S01-0000]")


def test_zero_duration_is_warning_and_kept() -> None:
    result = parse_episode_text(_one_scene(event("CAMERA", "00:05.00", "00:05.00")))
    assert result.ok and "zero_duration_event" in codes(result, Severity.WARNING)
    assert result.episodes[0].events[0].duration_ms == 0


def test_unrecognized_line_is_preserved_and_reported() -> None:
    text = _one_scene(event("CAMERA", "00:00.00", "00:20.00") + "\nA stray note.\n")
    result = parse_episode_text(text)
    assert result.ok and codes(result) == ["unrecognized_line"]
    assert result.episodes[0].scenes[0].unparsed_lines[0].text == "A stray note."
    assert "A stray note" not in result.issues[0].message


def test_unquoted_dialogue_is_flagged() -> None:
    result = parse_episode_text(
        _one_scene(event("MUSIC", "00:00.00", "00:20.00", "Strings swell."))
    )
    assert result.ok and codes(result) == ["dialogue_unquoted"]


def test_unbalanced_speaker_annotation_keeps_whole_label() -> None:
    result = parse_episode_text(_one_scene(event("NAME (Calm", "00:00.00", "00:05.00", '"Hi."')))
    assert codes(result) == ["speaker_label_unparsed"]
    assert result.episodes[0].events[0].speaker == "NAME (Calm"


@pytest.mark.parametrize(
    ("label", "speaker", "annotations"),
    [
        ("NAME", "NAME", ()),
        ("NAME (Calm)", "NAME", ("Calm",)),
        ("DR. A-B (V.O.) (Low, Tense)", "DR. A-B", ("V.O.", "Low, Tense")),
        ("THE OLD MAN'S SON (O.S.)", "THE OLD MAN'S SON", ("O.S.",)),
    ],
)
def test_speaker_annotations(label: str, speaker: str, annotations: tuple[str, ...]) -> None:
    ep = only_episode(_one_scene(event(label, "00:00.00", "00:05.00", '"Hi."')))
    assert (ep.events[0].speaker, ep.events[0].annotations, ep.events[0].label) == (
        speaker,
        annotations,
        label,
    )


def test_diagnostics_never_quote_script_text() -> None:
    secret = "ZEBRA-SECRET-LINE"
    text = _one_scene(
        event("A (x)", "00:00.00", "00:10.00", f'"{secret}"'),
        event("B (y)", "00:05.00", "00:04.00", f'"{secret}"'),
        f"**{secret}\n",
    )
    result = parse_episode_text(text)
    assert result.issues
    assert all(secret not in str(issue) for issue in result.issues)


# --- determinism and serialization ------------------------------------------


def test_parsing_is_deterministic() -> None:
    first = parse_path(FIXTURE).model_dump_json()
    second = parse_path(FIXTURE).model_dump_json()
    assert first == second


def test_json_round_trip() -> None:
    result = parse_path(FIXTURE)
    restored = ParseResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.episodes[0].events[0].event_type is EventType.CAMERA


def test_models_are_frozen() -> None:
    ep = parse_path(FIXTURE).episodes[0]
    with pytest.raises(pydantic.ValidationError):
        ep.events[0].start_ms = 1  # type: ignore[misc]


def test_model_rejects_reversed_event_directly() -> None:
    ev = parse_path(FIXTURE).episodes[0].events[0]
    with pytest.raises(pydantic.ValidationError):
        type(ev).model_validate({**ev.model_dump(), "start_ms": 9_000, "end_ms": 1_000})
