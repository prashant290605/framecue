"""Candidate discovery tests. Expectations are derived by hand from each synthetic timeline,
not copied from the implementation's output."""

import pydantic
import pytest

from promolens.candidates import (
    Anchor,
    EpisodeIndex,
    build_candidate,
    context_after,
    context_before,
    ending_evidence,
    find_end_anchors,
    find_start_anchors,
    generate_candidates,
    generate_pack_candidates,
    generate_pool,
    intersects,
    opening_evidence,
    rank_key,
    select_shortlist,
    summarize,
    validate_candidates,
)
from promolens.schemas import (
    CandidateConfig,
    ClipSegment,
    EventType,
    HeuristicAssessment,
    PromoCandidate,
)
from synthetic import build, ev, shots

CFG = CandidateConfig()
NO_GRID = CandidateConfig(fallback_step_ms=10_000_000)


def windows(candidates) -> set[tuple[int, int]]:
    return {(c.start_ms, c.end_ms) for c in candidates}


def pool_of(episode, config=CFG) -> list[PromoCandidate]:
    return generate_pool(episode, config)[4]


def dialogue_scene(duration_ms: int) -> list[str]:
    """Shots every 4 s and a neutral two-person exchange: a 2 s line every 5 s."""
    lines = [
        ev("ANNA (Plain)" if i % 2 else "BEN (Plain)", t, t + 2_000, "We should check the list.")
        for i, t in enumerate(range(500, duration_ms - 2_000, 5_000))
    ]
    return shots(0, duration_ms, 4_000) + lines


# --- 1. basic discovery -------------------------------------------------------


def test_basic_discovery_produces_valid_event_aligned_windows() -> None:
    episode = build([(0, 90_000, dialogue_scene(90_000))])
    result = generate_candidates(episode)
    assert 1 <= len(result.shortlist) <= CFG.max_per_episode
    source_times = {t for e in episode.events for t in (e.start_ms, e.end_ms)} | {0, 90_000}
    for c in result.shortlist:
        assert 20_000 <= c.duration_ms <= 60_000
        assert c.start_ms in source_times and c.end_ms in source_times
        assert c.heuristic.method == "heuristic_baseline_v1"
    assert result.raw_window_count >= result.pool_size >= len(result.shortlist)
    assert validate_candidates(episode, result.shortlist, CFG) == []


# --- 2-4. duration limits -------------------------------------------------------


def test_exact_minimum_duration_is_accepted() -> None:
    # Shots 0-0.01 and 0.01-20.00. The only start that leaves 20 s is 0; the only end is 20.00.
    episode = build([(0, 20_000, [ev("CAMERA", 0, 10), ev("CAMERA", 10, 20_000)])])
    assert windows(pool_of(episode, NO_GRID)) == {(0, 20_000)}
    assert windows(generate_candidates(episode, NO_GRID).shortlist) == {(0, 20_000)}


def test_exact_maximum_duration_is_accepted_and_longer_is_not() -> None:
    # Ends: only 61.00. Starts: 0 (61 s, too long) and 1.00 (exactly 60 s).
    episode = build([(0, 61_000, [ev("CAMERA", 0, 1_000), ev("CAMERA", 1_000, 61_000)])])
    assert windows(pool_of(episode, NO_GRID)) == {(1_000, 61_000)}


def test_invalid_durations_are_rejected_by_validation() -> None:
    episode = build([(0, 90_000, dialogue_scene(90_000))])
    good = generate_candidates(episode).shortlist[0]
    for start, end in ((0, 19_990), (0, 60_010)):
        bad = _with_window(good, start, end)
        assert any("outside allowed range" in p for p in validate_candidates(episode, [bad], CFG))
    with pytest.raises(pydantic.ValidationError):
        ClipSegment(episode_number=1, start_ms=5_000, end_ms=5_000)
    with pytest.raises(pydantic.ValidationError):
        CandidateConfig(min_duration_ms=30_000, max_duration_ms=20_000)


def test_episode_shorter_than_minimum_gives_no_candidates() -> None:
    episode = build([(0, 19_990, [ev("CAMERA", 0, 19_990)])])
    result = generate_candidates(episode)
    assert result.shortlist == () and result.pool_size == 0 and result.raw_window_count == 0


# --- 5-8. timeline structure ----------------------------------------------------


def test_clips_can_span_scene_boundaries() -> None:
    episode = build(
        [
            (0, 15_000, [ev("CAMERA", 0, 15_000)]),
            (15_000, 40_000, [ev("CAMERA", 15_000, 40_000)]),
        ]
    )
    crossing = [c for c in pool_of(episode, NO_GRID) if c.start_ms < 15_000 < c.end_ms]
    assert windows(crossing) == {(0, 40_000)}  # only 0->40.00 is 20-60 s and crosses
    assert crossing[0].signals.scene_numbers == (1, 2)
    assert crossing[0].signals.crosses_scene
    assert validate_candidates(episode, crossing, NO_GRID) == []


def test_concurrent_events_are_normal_and_all_visible() -> None:
    episode = build(
        [
            (
                0,
                40_000,
                [
                    ev("CAMERA", 0, 40_000),
                    ev("ACTION", 0, 10_000, "Two people argue across a table."),
                    ev("SOUND FX", 500, 1_500, "A door slams."),
                    ev("CARA (Firm)", 1_000, 2_500, "Sit down."),
                ],
            )
        ]
    )
    candidate = next(c for c in pool_of(episode, NO_GRID) if c.start_ms == 0)
    types = {e.event_type for e in opening_evidence(episode, candidate)}
    assert types == {EventType.CAMERA, EventType.ACTION, EventType.SOUND_FX, EventType.DIALOGUE}
    assert candidate.signals.dialogue_boundary_conflicts == 0


def test_source_order_does_not_change_windows() -> None:
    blocks = [
        ev("CAMERA", 0, 12_000),
        ev("CAMERA", 12_000, 45_000),
        ev("DAN (Calm)", 3_000, 5_000, "Fine."),
        ev("ACTION", 20_000, 24_000, "He leaves."),
    ]
    in_order = build([(0, 45_000, blocks)])
    shuffled = build([(0, 45_000, [blocks[3], blocks[1], blocks[2], blocks[0]])])
    assert windows(pool_of(in_order)) == windows(pool_of(shuffled))
    assert windows(generate_candidates(in_order).shortlist) == windows(
        generate_candidates(shuffled).shortlist
    )


def test_cuts_never_split_dialogue() -> None:
    # A camera cut at 32.00 falls inside a line spoken from 30.00 to 34.00. Both 30.00 and
    # 34.00 can serve as a start (earliest end 50.00) or an end (at least 20 s in).
    episode = build(
        [
            (
                0,
                90_000,
                [
                    ev("CAMERA", 0, 32_000),
                    ev("CAMERA", 32_000, 90_000),
                    ev("EVE (Low)", 30_000, 34_000, "Not here."),
                ],
            )
        ]
    )
    index = EpisodeIndex(episode)
    starts = {a.time_ms for a in find_start_anchors(index, CFG)}
    ends = {a.time_ms for a in find_end_anchors(index, CFG)}
    assert 32_000 not in starts and 32_000 not in ends
    assert 30_000 in starts  # the line's onset
    assert 30_000 in ends and 34_000 in ends  # just before the line, and just after it
    for c in pool_of(episode):
        assert not 30_000 < c.start_ms < 34_000
        assert not 30_000 < c.end_ms < 34_000
        assert c.signals.dialogue_boundary_conflicts == 0


def test_evenly_spaced_sampling_would_split_the_answer() -> None:
    # A question ends at 37.30 and the answer runs 37.80-41.00. A naive 10 s grid would cut at
    # 40.00, inside the answer. The generator must offer a cut right before the answer instead.
    episode = build(
        [
            (
                0,
                80_000,
                [
                    *shots(0, 80_000, 5_000),
                    ev("FAY (Quiet)", 34_000, 37_300, "Where were you last night?"),
                    ev("GUS (Quiet)", 37_800, 41_000, "Out walking."),
                ],
            )
        ]
    )
    pool = pool_of(episode)
    assert all(c.end_ms != 40_000 for c in pool)
    before_answer = [c for c in pool if c.end_ms == 37_800]
    assert before_answer and all("before_answer" in c.end_anchors for c in before_answer)
    assert "question" in before_answer[0].signals.ending_cues
    assert "cut_before_line" in before_answer[0].signals.ending_cues


# --- 9-10. boundary evidence (half-open intervals) --------------------------------


def _episode_for_evidence():
    return build(
        [
            (
                0,
                60_000,
                [
                    ev("CAMERA", 0, 60_000),
                    ev("ACTION", 8_000, 10_000, "Ends exactly at the clip start."),
                    ev("ACTION", 9_000, 11_000, "Already running when the clip starts."),
                    ev("ACTION", 12_990, 14_000, "Starts inside the hook window."),
                    ev("ACTION", 13_000, 15_000, "Starts exactly at start + 3 s."),
                    ev("ACTION", 30_000, 35_000, "Ends exactly where the ending window opens."),
                    ev("ACTION", 34_000, 41_000, "Runs past the clip end."),
                    ev("ACTION", 40_000, 42_000, "Starts exactly at the clip end."),
                ],
            )
        ]
    )


def _candidate(episode, start_ms: int, end_ms: int) -> PromoCandidate:
    index = EpisodeIndex(episode)
    return build_candidate(index, Anchor(start_ms, ("test",)), Anchor(end_ms, ("test",)), CFG)


def test_opening_evidence_uses_half_open_first_three_seconds() -> None:
    episode = _episode_for_evidence()
    candidate = _candidate(episode, 10_000, 40_000)
    texts = {
        e.text for e in opening_evidence(episode, candidate) if e.event_type is EventType.ACTION
    }
    assert texts == {"Already running when the clip starts.", "Starts inside the hook window."}
    assert candidate.opening_event_ids == tuple(
        e.event_id for e in opening_evidence(episode, candidate)
    )


def test_ending_evidence_uses_half_open_final_five_seconds() -> None:
    episode = _episode_for_evidence()
    candidate = _candidate(episode, 10_000, 40_000)
    texts = {
        e.text for e in ending_evidence(episode, candidate) if e.event_type is EventType.ACTION
    }
    assert texts == {"Runs past the clip end."}
    assert candidate.signals.straddling_events == 3  # camera, the 9-11 action, the 34-41 action


def test_context_helpers_report_neighbouring_events() -> None:
    episode = _episode_for_evidence()
    candidate = _candidate(episode, 10_000, 40_000)
    before = {
        e.text
        for e in context_before(episode, candidate, 2_000)
        if e.event_type is EventType.ACTION
    }
    after = {
        e.text for e in context_after(episode, candidate, 2_000) if e.event_type is EventType.ACTION
    }
    assert before == {"Ends exactly at the clip start.", "Already running when the clip starts."}
    assert after == {"Runs past the clip end.", "Starts exactly at the clip end."}


def test_intersects_semantics() -> None:
    episode = build(
        [(0, 30_000, [ev("CAMERA", 0, 30_000), ev("SOUND FX", 5_000, 5_000, "Click.")])]
    )
    camera, click = episode.events
    assert intersects(camera, 29_999, 40_000) and not intersects(camera, 30_000, 40_000)
    assert intersects(click, 5_000, 6_000) and not intersects(click, 4_000, 5_000)


# --- 11-12. deduplication and diversity ------------------------------------------


def _scored(candidate: PromoCandidate, priority: float) -> PromoCandidate:
    heuristic = HeuristicAssessment(method="test", priority=priority, components={})
    return candidate.model_copy(update={"heuristic": heuristic})


def test_near_duplicates_are_suppressed_but_real_alternatives_survive() -> None:
    episode = build([(0, 120_000, shots(0, 120_000, 500))])
    a = _scored(_candidate(episode, 0, 30_000), 0.9)
    near = _scored(_candidate(episode, 500, 30_000), 0.89)  # moves the start by only 0.5 s
    alt = _scored(_candidate(episode, 0, 34_000), 0.88)  # moves the end by 4 s
    config = CandidateConfig(regions=1)
    chosen = select_shortlist([near, alt, a], 120_000, config)
    assert windows(chosen) == {(0, 30_000), (0, 34_000)}
    assert chosen[1].selection_reason == f"alternative_cut_of:{a.candidate_id}"


def test_alternative_cuts_per_moment_are_capped() -> None:
    episode = build([(0, 120_000, shots(0, 120_000, 500))])
    base = [_scored(_candidate(episode, 0, 30_000 + k * 3_000), 0.9 - k / 100) for k in range(6)]
    config = CandidateConfig(regions=1, alternatives_per_moment=2, reserved_alternative_slots=0,
                             max_per_episode=3)  # fmt: skip
    assert len(select_shortlist(base, 120_000, config)) == 3


def test_shortlist_covers_early_middle_and_late() -> None:
    # The first 40 s are full of exclamations and intense delivery; the rest is calm. Pure
    # priority ranking would pack every slot into the opening.
    hot = [
        ev("HAL (Shouting, Furious)", t, t + 1_500, "Get out now!")
        for t in range(500, 40_000, 2_000)
    ]
    calm = [
        ev("IVY (Calm)", t, t + 2_000, "The train is at nine.")
        for t in range(41_000, 178_000, 6_000)
    ]
    episode = build([(0, 180_000, shots(0, 180_000, 3_000) + hot + calm)])
    shortlist = generate_candidates(episode).shortlist
    thirds = {min(2, ((c.start_ms + c.end_ms) // 2) * 3 // 180_000) for c in shortlist}
    assert thirds == {0, 1, 2}
    assert summarize([generate_candidates(episode)], CFG)["episodes_with_uncovered_region"] == []


def test_best_episode_ending_is_always_considered() -> None:
    episode = build([(0, 150_000, dialogue_scene(150_000))])
    assert any(c.end_ms == 150_000 for c in generate_candidates(episode).shortlist)


# --- 13-15. recall without keywords, visual beats, sparse timelines ---------------


def test_candidates_without_any_dramatic_keywords() -> None:
    episode = build([(0, 120_000, dialogue_scene(120_000))])
    assert all(not c.signals.opening_cues or set(c.signals.opening_cues) <= {"question"}
               for c in pool_of(episode))  # fmt: skip
    result = generate_candidates(episode)
    assert len(result.shortlist) == CFG.max_per_episode
    stats = summarize([result], CFG)
    assert stats["episodes_with_uncovered_region"] == []
    assert stats["coverage_min_median_max"][0] >= 0.9


def test_visual_action_without_dialogue_is_an_anchor() -> None:
    episode = build(
        [
            (
                0,
                90_000,
                [*shots(0, 90_000, 6_000), ev("ACTION", 30_000, 32_000, "She slaps the table.")],
            )
        ]
    )
    index = EpisodeIndex(episode)
    at_30 = next(a for a in find_start_anchors(index, CFG) if a.time_ms == 30_000)
    assert {"action_onset", "cue:physical_action", "camera_cut"} <= set(at_30.reasons)
    starting = [c for c in pool_of(episode) if c.start_ms == 30_000]
    assert starting and "physical_action" in starting[0].signals.opening_cues


def test_sparse_episode_uses_fallback_anchors() -> None:
    # One 100 s shot and nothing else: without fallback anchors no 20-60 s window exists.
    episode = build([(0, 100_000, [ev("CAMERA", 0, 100_000)])])
    assert generate_candidates(episode, NO_GRID).shortlist == ()
    result = generate_candidates(episode)
    assert result.shortlist
    assert all("fallback_grid" in c.start_anchors + c.end_anchors for c in result.shortlist)
    assert validate_candidates(episode, result.shortlist, CFG) == []


# --- 16. empty / malformed input ------------------------------------------------


def test_empty_pack_and_foreign_candidates() -> None:
    assert generate_pack_candidates([]) == []
    assert summarize([], CFG)["shortlisted"] == 0
    episode = build([(0, 90_000, dialogue_scene(90_000))])
    other = build([(0, 90_000, dialogue_scene(90_000))], number=2)
    foreign = generate_candidates(other).shortlist[0]
    assert any("belongs to episode 2" in p for p in validate_candidates(episode, [foreign], CFG))


def test_validation_catches_fabricated_boundaries_and_evidence() -> None:
    episode = build([(0, 90_000, dialogue_scene(90_000))])
    good = generate_candidates(episode).shortlist[0]
    off_grid = _with_window(good, good.start_ms + 7, good.end_ms)
    assert any("not a source timing" in p for p in validate_candidates(episode, [off_grid], CFG))
    fake = good.model_copy(update={"opening_event_ids": ("E001-S01-9999",)})
    assert any("unknown event ids" in p for p in validate_candidates(episode, [fake], CFG))
    assert any("duplicate" in p for p in validate_candidates(episode, [good, good], CFG))
    with pytest.raises(pydantic.ValidationError):
        PromoCandidate.model_validate({**good.model_dump(), "candidate_id": "C001-1-2"})
    with pytest.raises(pydantic.ValidationError):  # v1: exactly one segment
        PromoCandidate.model_validate({**good.model_dump(), "segments": []})


# --- 17-19. limits, determinism, ordering -----------------------------------------


@pytest.mark.parametrize("limit", [1, 3, 7])
def test_configurable_candidate_limit(limit: int) -> None:
    episode = build([(0, 150_000, dialogue_scene(150_000))])
    shortlist = generate_candidates(episode, CandidateConfig(max_per_episode=limit)).shortlist
    assert len(shortlist) == limit


def test_ids_are_deterministic_and_runs_repeat_exactly() -> None:
    episode = build([(0, 61_000, [ev("CAMERA", 0, 1_000), ev("CAMERA", 1_000, 61_000)])])
    assert [c.candidate_id for c in pool_of(episode, NO_GRID)] == ["C001-0001000-0061000"]
    big = build([(0, 150_000, dialogue_scene(150_000))])
    assert generate_candidates(big).model_dump_json() == generate_candidates(big).model_dump_json()


def test_heuristic_sorting_is_stable_with_ties() -> None:
    episode = build([(0, 200_000, shots(0, 200_000, 1_000))])
    tied = [_scored(_candidate(episode, s, s + 30_000), 0.5) for s in (120_000, 0, 60_000)] + [
        _scored(_candidate(episode, 150_000, 180_000), 0.7)
    ]
    ordered = sorted(tied, key=rank_key)
    assert [c.start_ms for c in ordered] == [150_000, 0, 60_000, 120_000]
    chosen = select_shortlist(tied, 200_000, CandidateConfig(regions=1))
    assert [c.start_ms for c in chosen] == [150_000, 0, 60_000, 120_000]


def test_shortlist_is_sorted_by_priority() -> None:
    episode = build([(0, 150_000, dialogue_scene(150_000))])
    shortlist = generate_candidates(episode).shortlist
    assert list(shortlist) == sorted(shortlist, key=rank_key)


# --- helpers ----------------------------------------------------------------------


def _with_window(candidate: PromoCandidate, start_ms: int, end_ms: int) -> PromoCandidate:
    """Rebuild a candidate around a new window, bypassing generation (for validation tests)."""
    from promolens.schemas import candidate_id_for

    segment = ClipSegment(episode_number=candidate.episode_number, start_ms=start_ms, end_ms=end_ms)
    return candidate.model_copy(
        update={
            "candidate_id": candidate_id_for(candidate.episode_number, start_ms, end_ms),
            "segments": (segment,),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
        }
    )


def test_shot_end_shortly_after_a_line_is_a_clean_end_anchor() -> None:
    # Line ends 23.20 and the shot cuts at 24.00 (0.8 s later): a cleaner end on the same beat.
    def anchors(extra: list[str]) -> dict[int, tuple[str, ...]]:
        blocks = [ev("CAMERA", 0, 24_000), ev("CAMERA", 24_000, 60_000), *extra]
        index = EpisodeIndex(build([(0, 60_000, blocks)]))
        return {a.time_ms: a.reasons for a in find_end_anchors(index, CFG)}

    plain = anchors([ev("JO (Calm)", 21_000, 23_200, "It is done.")])
    assert "shot_end_after_line" in plain[24_000]
    question = anchors([ev("JO (Calm)", 21_000, 23_200, "Is it done?")])
    assert "shot_end_after_open_line" in question[24_000]
    too_late = anchors([ev("JO (Calm)", 21_000, 22_000, "It is done.")])  # 2.0 s > 1.5 s limit
    assert not any(r.startswith("shot_end_after") for r in too_late[24_000])
