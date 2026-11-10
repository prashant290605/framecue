import pytest

from framecue.baseline import METHOD, assess, duration_preference, event_cues
from framecue.schemas import BaselineWeights, CandidateConfig, CandidateSignals
from synthetic import build, ev

CFG = CandidateConfig()


def signals(**overrides) -> CandidateSignals:
    base = dict(
        opening_onsets=0,
        dialogue_lines=0,
        speakers=0,
        non_camera_events=0,
        camera_cuts=0,
        max_quiet_ms=0,
        scene_numbers=(1,),
        starts_at_scene_start=False,
        ends_at_scene_end=False,
        clean_start_cut=False,
        clean_end_cut=False,
        straddling_events=0,
        dialogue_boundary_conflicts=0,
    )
    return CandidateSignals(**{**base, **overrides})


def _cues(label: str, text: str) -> set[str]:
    episode = build([(0, 30_000, [ev("CAMERA", 0, 30_000), ev(label, 1_000, 2_000, text)])])
    return set(event_cues(episode.events[1]))


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        ("A (Calm)", "Where is it?", {"question"}),
        ("A (Calm)", "Stop!", {"exclamation"}),
        ("A (Calm)", "I thought...", {"trailing_off"}),
        ("A (Screaming)", "Fine.", {"intense_delivery"}),
        ("A (Calm)", "The plain truth.", {"reveal_cue"}),
        ("A (Calm)", "I will slap you.", set()),  # physical cues come from visuals, not speech
        ("ACTION", "She slaps the table.", {"physical_action"}),
        ("CAMERA", "Wide shot of a quiet street.", set()),
        ("SOUND FX", "A low hum.", {"sound_fx"}),
    ],
)
def test_event_cues(label: str, text: str, expected: set[str]) -> None:
    assert _cues(label, text) == expected


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [(20_000, 0.5), (25_000, 0.75), (30_000, 1.0), (45_000, 1.0), (52_500, 0.75), (60_000, 0.5)],
)
def test_duration_preference(duration_ms: int, expected: float) -> None:
    assert duration_preference(duration_ms, CFG) == pytest.approx(expected)


def test_assessment_is_labelled_bounded_and_deterministic() -> None:
    s = signals(opening_onsets=1, opening_cues=("question",), dialogue_lines=4, speakers=2)
    first, second = assess(s, 30_000, CFG), assess(s, 30_000, CFG)
    assert first == second
    assert first.method == METHOD
    assert 0.0 <= first.priority <= 1.0
    assert set(first.components) == {
        "opening",
        "ending",
        "activity",
        "boundary",
        "duration",
        "context",
    }
    assert all(0.0 <= v <= 1.0 for v in first.components.values())


def test_open_ending_scores_above_closed_ending() -> None:
    closed = assess(signals(), 30_000, CFG).components["ending"]
    opened = assess(signals(ending_cues=("cut_before_line", "question")), 30_000, CFG)
    assert closed == 0.0 and opened.components["ending"] == pytest.approx(0.65)


def test_dialogue_conflict_halves_boundary_score() -> None:
    clean = signals(clean_start_cut=True, clean_end_cut=True)
    assert assess(clean, 30_000, CFG).components["boundary"] == pytest.approx(1.0)
    split = signals(clean_start_cut=True, clean_end_cut=True, dialogue_boundary_conflicts=1)
    assert assess(split, 30_000, CFG).components["boundary"] == pytest.approx(0.5)


def test_weights_are_configurable() -> None:
    only_duration = BaselineWeights(
        opening=0, ending=0, activity=0, boundary=0, context=0, duration=1
    )
    config = CandidateConfig(weights=only_duration)
    assert assess(signals(), 25_000, config).priority == pytest.approx(0.75)
    with pytest.raises(ValueError):
        BaselineWeights(opening=0, ending=0, activity=0, boundary=0, duration=0, context=0)


def test_long_quiet_stretch_lowers_activity() -> None:
    busy = assess(signals(dialogue_lines=6, non_camera_events=9), 30_000, CFG)
    quiet = assess(signals(dialogue_lines=6, non_camera_events=9, max_quiet_ms=12_000), 30_000, CFG)
    assert busy.components["activity"] == pytest.approx(1.0)
    assert quiet.components["activity"] == pytest.approx(0.7)
