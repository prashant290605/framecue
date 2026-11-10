"""Deterministic discovery of promo candidates within single episodes.

All times are integer milliseconds. Clips are half-open intervals ``[start_ms, end_ms)``; see
``intersects`` for the event-overlap rule.

Pipeline for one episode:

1. **Anchors.**
   - Start anchors: the episode start, scene starts, and the onset of every event (camera
     cut, dialogue, action, sound). Each is tagged with weak cue categories.
   - End anchors: the episode end, scene ends, the end of every event, and the instant just
     before each dialogue line ("before_line"). It becomes "before_answer" when the previous
     line was a question.
   - Fallback grid anchors fill long stretches that have no events.
   - Any anchor that falls inside a dialogue line is dropped, so cuts never split speech.
2. **Raw windows.** Every start/end anchor pair whose duration fits the range is counted.
   Raw windows are counted, never built.
3. **Pool.** The pool is bounded: at most ``duration_bands * ends_per_band + 1`` windows per
   start. For each start anchor it keeps:
   - the best ``ends_per_band`` ends in each of ``duration_bands`` duration bands, ranked by
     end-anchor quality and duration preference;
   - the episode end, whenever it is in range.
4. **Score.** Every pool window gets a heuristic baseline priority (see ``baseline``).
5. **Shortlist.** Selection is deterministic and diversity-aware:
   - First, the best window in each timeline region (early, middle, late).
   - Then a greedy pass in priority order. A window overlapping one already selected
     (IoU >= ``max_iou``) is skipped, unless it is one of a few alternative cuts for that
     moment that move a boundary by at least ``min_boundary_shift_ms``.

Keyword cues only influence ordering. Anchors and windows come from structure, so a story
with no "dramatic" vocabulary still gets full-timeline candidates.
"""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from framecue.baseline import OPEN_LINE_CUES, STRONG_CUES, assess, duration_preference, event_cues
from framecue.schemas import (
    CandidateConfig,
    CandidateSignals,
    ClipSegment,
    Episode,
    EpisodeCandidates,
    EventType,
    PromoCandidate,
    ScriptEvent,
    candidate_id_for,
)

FALLBACK = "fallback_grid"
END_REASON_QUALITY = {
    "episode_end": 1.0,
    "before_answer": 0.8,
    "shot_end_after_open_line": 0.75,
    "open_line_end": 0.7,
    "shot_end_after_line": 0.6,
    "scene_end": 0.6,
    "before_line": 0.5,
    "line_end": 0.5,
    "sound_end": 0.4,
    "camera_cut": 0.3,
    "action_end": 0.3,
    FALLBACK: 0.0,
}
_ONSET_REASON = {
    EventType.CAMERA: "camera_cut",
    EventType.DIALOGUE: "dialogue_onset",
    EventType.ACTION: "action_onset",
    EventType.SOUND_FX: "sound_onset",
}
_END_REASON = {
    EventType.CAMERA: "camera_cut",
    EventType.DIALOGUE: "line_end",
    EventType.ACTION: "action_end",
    EventType.SOUND_FX: "sound_end",
}


class CandidateValidationError(ValueError):
    """Raised when generated candidates violate an invariant. This indicates a bug."""


@dataclass(frozen=True)
class Anchor:
    time_ms: int
    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------


def intersects(event: ScriptEvent, start_ms: int, end_ms: int) -> bool:
    """Half-open intersection of an event with ``[start_ms, end_ms)``.

    An event that starts before the window and ends inside it intersects. So does an event
    that starts inside and runs past the end. A zero-length event at t intersects when
    ``start_ms <= t < end_ms``.
    """
    if event.start_ms == event.end_ms:
        return start_ms <= event.start_ms < end_ms
    return event.start_ms < end_ms and event.end_ms > start_ms


def iou(a: PromoCandidate, b: PromoCandidate) -> float:
    """Temporal intersection-over-union of two single-span candidates (0 across episodes)."""
    if a.episode_number != b.episode_number:
        return 0.0
    inter = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    return inter / (a.duration_ms + b.duration_ms - inter)


class EpisodeIndex:
    """A chronological event index for one episode, with bounded interval queries."""

    def __init__(self, episode: Episode) -> None:
        self.episode = episode
        self.duration_ms = episode.duration_ms
        self.timeline = episode.timeline()
        self._starts = [event.start_ms for event in self.timeline]
        self._longest = max((event.duration_ms for event in self.timeline), default=0)
        self.dialogue = [e for e in self.timeline if e.event_type is EventType.DIALOGUE]
        self.scene_starts = {scene.start_ms for scene in episode.scenes}
        self.scene_ends = {scene.end_ms for scene in episode.scenes}
        cameras = [e for e in self.timeline if e.event_type is EventType.CAMERA]
        self.camera_starts = {e.start_ms for e in cameras}
        self.camera_ends = {e.end_ms for e in cameras}
        self.boundary_times = frozenset(
            {t for e in self.timeline for t in (e.start_ms, e.end_ms)}
            | self.scene_starts
            | self.scene_ends
        )
        self.cues = {e.event_id: event_cues(e) for e in self.timeline}

    def intersecting(self, start_ms: int, end_ms: int) -> list[ScriptEvent]:
        """Events intersecting ``[start_ms, end_ms)``, in timeline order."""
        lo = bisect_left(self._starts, start_ms - self._longest)
        hi = bisect_left(self._starts, end_ms)
        return [e for e in self.timeline[lo:hi] if intersects(e, start_ms, end_ms)]

    def straddling(self, time_ms: int) -> list[ScriptEvent]:
        """Events that a cut at ``time_ms`` would slice (start < t < end)."""
        lo = bisect_left(self._starts, time_ms - self._longest)
        hi = bisect_left(self._starts, time_ms)
        return [e for e in self.timeline[lo:hi] if e.end_ms > time_ms]

    def dialogue_safe(self, time_ms: int) -> bool:
        return not any(e.event_type is EventType.DIALOGUE for e in self.straddling(time_ms))


# ---------------------------------------------------------------------------
# Evidence helpers (return events in memory; candidates store only ids)
# ---------------------------------------------------------------------------


def opening_evidence(
    episode: Episode, candidate: PromoCandidate, window_ms: int = 3_000
) -> list[ScriptEvent]:
    """Events intersecting the first ``window_ms`` of the clip, including events already
    in progress when the clip starts."""
    start = candidate.segments[0].start_ms
    end = min(candidate.segments[0].end_ms, start + window_ms)
    return EpisodeIndex(episode).intersecting(start, end)


def ending_evidence(
    episode: Episode, candidate: PromoCandidate, window_ms: int = 5_000
) -> list[ScriptEvent]:
    """Events intersecting the last ``window_ms`` of the clip, including events cut by the end."""
    end = candidate.segments[-1].end_ms
    start = max(candidate.segments[-1].start_ms, end - window_ms)
    return EpisodeIndex(episode).intersecting(start, end)


def context_before(
    episode: Episode, candidate: PromoCandidate, window_ms: int = 10_000
) -> list[ScriptEvent]:
    """Events intersecting ``[start - window_ms, start)``: what a viewer would have missed."""
    start = candidate.segments[0].start_ms
    return EpisodeIndex(episode).intersecting(max(0, start - window_ms), start)


def context_after(
    episode: Episode, candidate: PromoCandidate, window_ms: int = 10_000
) -> list[ScriptEvent]:
    """Events intersecting ``[end, end + window_ms)``: what the cut withholds."""
    end = candidate.segments[-1].end_ms
    return EpisodeIndex(episode).intersecting(end, end + window_ms)


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def find_start_anchors(index: EpisodeIndex, config: CandidateConfig) -> list[Anchor]:
    reasons: dict[int, set[str]] = defaultdict(set)
    reasons[0].add("episode_start")
    for time_ms in index.scene_starts:
        reasons[time_ms].add("scene_start")
    for event in index.timeline:
        reasons[event.start_ms].add(_ONSET_REASON[event.event_type])
        reasons[event.start_ms].update(f"cue:{cue}" for cue in index.cues[event.event_id])
    latest = index.duration_ms - config.min_duration_ms
    _add_fallback(reasons, index, config, 0, latest)
    return _safe_anchors(reasons, index, 0, latest)


def find_end_anchors(index: EpisodeIndex, config: CandidateConfig) -> list[Anchor]:
    reasons: dict[int, set[str]] = defaultdict(set)
    reasons[index.duration_ms].add("episode_end")
    for time_ms in index.scene_ends:
        reasons[time_ms].add("scene_end")
    for event in index.timeline:
        reasons[event.end_ms].add(_END_REASON[event.event_type])
        if event.event_type is EventType.DIALOGUE:
            reasons[event.start_ms].add("before_line")
            if index.cues[event.event_id] & OPEN_LINE_CUES:
                reasons[event.end_ms].add("open_line_end")
    for previous, line in zip(index.dialogue, index.dialogue[1:], strict=False):
        if "question" in index.cues[previous.event_id] and line.start_ms >= previous.end_ms:
            reasons[line.start_ms].add("before_answer")
    _add_shot_ends_after_lines(reasons, index, config)
    earliest = config.min_duration_ms
    _add_fallback(reasons, index, config, earliest, index.duration_ms)
    return _safe_anchors(reasons, index, earliest, index.duration_ms)


def _add_shot_ends_after_lines(
    reasons: dict[int, set[str]], index: EpisodeIndex, config: CandidateConfig
) -> None:
    """Tag camera-shot ends that come soon after the latest completed line.

    Ending exactly when a line ends often cuts the current shot mid-way. The shot's own end,
    up to ``settle_after_line_ms`` later, gives a cleaner cut that lands on the same beat.
    This was added after a real-pack run: 43% of shortlisted clips ended mid-shot.
    """
    for camera in index.timeline:
        if camera.event_type is not EventType.CAMERA:
            continue
        cut = camera.end_ms
        before = [d for d in index.dialogue if d.end_ms <= cut]
        if not before:
            continue
        line = max(before, key=lambda d: (d.end_ms, d.source_index))
        # A line still running at the cut makes the anchor unsafe; _safe_anchors drops it.
        if 0 < cut - line.end_ms <= config.settle_after_line_ms:
            open_line = index.cues[line.event_id] & OPEN_LINE_CUES
            reasons[cut].add("shot_end_after_open_line" if open_line else "shot_end_after_line")


def _add_fallback(
    reasons: dict[int, set[str]], index: EpisodeIndex, config: CandidateConfig, lo: int, hi: int
) -> None:
    """Add grid points every ``fallback_step_ms`` wherever no real anchor lies within half a
    step. This keeps sparse stretches (long shots with no events) reachable."""
    existing = sorted(reasons)
    half = config.fallback_step_ms // 2
    for time_ms in range(config.fallback_step_ms, index.duration_ms, config.fallback_step_ms):
        if not lo <= time_ms <= hi:
            continue
        pos = bisect_left(existing, time_ms)
        near = [existing[i] for i in (pos - 1, pos) if 0 <= i < len(existing)]
        if all(abs(t - time_ms) > half for t in near):
            reasons[time_ms].add(FALLBACK)


def _safe_anchors(
    reasons: dict[int, set[str]], index: EpisodeIndex, lo: int, hi: int
) -> list[Anchor]:
    return [
        Anchor(time_ms, tuple(sorted(tags)))
        for time_ms, tags in sorted(reasons.items())
        if lo <= time_ms <= hi and index.dialogue_safe(time_ms)
    ]


def end_anchor_quality(anchor: Anchor) -> float:
    """Pool-pruning score for an end anchor: its strongest reason, plus a small bonus when
    several structural reasons coincide (e.g. a line end that is also a camera cut)."""
    structural = [r for r in anchor.reasons if r in END_REASON_QUALITY]
    if not structural:
        return 0.0
    best = max(END_REASON_QUALITY[r] for r in structural)
    return min(1.0, best + 0.1 * (len(structural) - 1))


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


def pool_windows(
    starts: list[Anchor], ends: list[Anchor], config: CandidateConfig
) -> tuple[int, list[tuple[Anchor, Anchor]]]:
    """Return ``(raw_window_count, kept_windows)``, using bounded searches with bisect."""
    end_times = [anchor.time_ms for anchor in ends]
    quality = [end_anchor_quality(anchor) for anchor in ends]
    span = config.max_duration_ms - config.min_duration_ms + 1
    raw = 0
    kept: list[tuple[Anchor, Anchor]] = []
    for start in starts:
        lo = bisect_left(end_times, start.time_ms + config.min_duration_ms)
        hi = bisect_right(end_times, start.time_ms + config.max_duration_ms)
        raw += hi - lo
        bands: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
        for i in range(lo, hi):
            duration = end_times[i] - start.time_ms
            band = (duration - config.min_duration_ms) * config.duration_bands // span
            score = quality[i] + 0.5 * duration_preference(duration, config)
            bands[band].append((-score, end_times[i], i))
        chosen = {i for band in bands.values() for *_, i in sorted(band)[: config.ends_per_band]}
        chosen.update(i for i in range(lo, hi) if "episode_end" in ends[i].reasons)
        kept.extend((start, ends[i]) for i in sorted(chosen))
    return raw, kept


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------


def compute_signals(
    index: EpisodeIndex, start_ms: int, end_ms: int, config: CandidateConfig
) -> CandidateSignals:
    inside = index.intersecting(start_ms, end_ms)
    whole = [e for e in inside if e.start_ms >= start_ms and e.end_ms <= end_ms]
    lines = [e for e in whole if e.event_type is EventType.DIALOGUE]
    opening = index.intersecting(start_ms, min(end_ms, start_ms + config.hook_window_ms))
    ending_start = max(start_ms, end_ms - config.ending_window_ms)
    ending = index.intersecting(ending_start, end_ms)
    cut = index.straddling(start_ms) + index.straddling(end_ms)
    scenes = tuple(
        s.scene_number for s in index.episode.scenes if s.start_ms < end_ms and s.end_ms > start_ms
    )
    return CandidateSignals(
        opening_onsets=sum(
            e.start_ms >= start_ms and e.event_type is not EventType.CAMERA for e in opening
        ),
        opening_cues=_union_cues(index, opening),
        ending_cues=_ending_cues(index, lines, ending, ending_start, end_ms, config),
        dialogue_lines=len(lines),
        speakers=len({e.speaker for e in lines}),
        non_camera_events=sum(e.event_type is not EventType.CAMERA for e in whole),
        camera_cuts=sum(
            e.event_type is EventType.CAMERA and start_ms < e.start_ms < end_ms for e in inside
        ),
        max_quiet_ms=_max_quiet(inside, start_ms, end_ms),
        scene_numbers=scenes,
        starts_at_scene_start=start_ms in index.scene_starts,
        ends_at_scene_end=end_ms in index.scene_ends,
        clean_start_cut=start_ms in index.camera_starts or start_ms in index.scene_starts,
        clean_end_cut=end_ms in index.camera_ends or end_ms in index.scene_ends,
        straddling_events=len({e.event_id for e in cut}),
        dialogue_boundary_conflicts=len(
            {e.event_id for e in cut if e.event_type is EventType.DIALOGUE}
        ),
    )


def _union_cues(index: EpisodeIndex, events: Iterable[ScriptEvent]) -> tuple[str, ...]:
    return tuple(sorted({cue for e in events for cue in index.cues[e.event_id]}))


def _ending_cues(
    index: EpisodeIndex,
    lines: list[ScriptEvent],
    ending: list[ScriptEvent],
    ending_start: int,
    end_ms: int,
    config: CandidateConfig,
) -> tuple[str, ...]:
    """Line-shape cues come only from the clip's final complete line, if it ends inside the
    ending window. Strong cues come from any event in the window. Structural cues come from
    where the cut falls."""
    cues: set[str] = {cue for e in ending for cue in index.cues[e.event_id] if cue in STRONG_CUES}
    final = max(
        (e for e in lines if e.end_ms > ending_start),
        key=lambda e: (e.end_ms, e.source_index),
        default=None,
    )
    if final is not None:
        cues |= index.cues[final.event_id] - STRONG_CUES
    if any(end_ms <= d.start_ms <= end_ms + config.cut_before_line_ms for d in index.dialogue):
        cues.add("cut_before_line")
    if end_ms == index.duration_ms:
        cues.add("episode_end")
    if end_ms in index.scene_ends:
        cues.add("scene_end")
    return tuple(sorted(cues))


def _max_quiet(events: list[ScriptEvent], start_ms: int, end_ms: int) -> int:
    """The longest stretch of ``[start, end)`` covered by no dialogue, action or sound event."""
    spans = sorted(
        (max(start_ms, e.start_ms), min(end_ms, e.end_ms))
        for e in events
        if e.event_type is not EventType.CAMERA
    )
    longest, cursor = 0, start_ms
    for lo, hi in spans:
        longest = max(longest, lo - cursor)
        cursor = max(cursor, hi)
    return max(longest, end_ms - cursor)


def build_candidate(
    index: EpisodeIndex, start: Anchor, end: Anchor, config: CandidateConfig
) -> PromoCandidate:
    a, b = start.time_ms, end.time_ms
    episode_number = index.episode.episode_number
    signals = compute_signals(index, a, b, config)
    opening = index.intersecting(a, min(b, a + config.hook_window_ms))
    ending = index.intersecting(max(a, b - config.ending_window_ms), b)
    return PromoCandidate(
        candidate_id=candidate_id_for(episode_number, a, b),
        episode_number=episode_number,
        segments=(ClipSegment(episode_number=episode_number, start_ms=a, end_ms=b),),
        start_ms=a,
        end_ms=b,
        duration_ms=b - a,
        start_anchors=start.reasons,
        end_anchors=end.reasons,
        opening_event_ids=tuple(e.event_id for e in opening),
        ending_event_ids=tuple(e.event_id for e in ending),
        signals=signals,
        heuristic=assess(signals, b - a, config),
    )


# ---------------------------------------------------------------------------
# Shortlist
# ---------------------------------------------------------------------------


def rank_key(candidate: PromoCandidate) -> tuple[float, int, int, int]:
    """Stable heuristic order: priority descending, then episode, start and end."""
    return (
        -candidate.heuristic.priority,
        candidate.episode_number,
        candidate.start_ms,
        candidate.end_ms,
    )


def select_shortlist(
    pool: list[PromoCandidate], duration_ms: int, config: CandidateConfig
) -> list[PromoCandidate]:
    """Pick up to ``max_per_episode`` candidates that are diverse but keep alternative cuts.

    The selection runs in passes, all in stable priority order:

    1. Seeds:
       - the best window in each timeline region (early, middle, late), so one scene cannot
         take every slot;
       - the best window that ends at the episode end, because episodes are written to end
         on a cliffhanger.
    2. Distinct moments only. A window may not overlap any chosen one with IoU >= ``max_iou``.
       This pass stops ``reserved_alternative_slots`` short of the limit.
    3. Distinct moments, plus up to ``alternatives_per_moment`` alternative cuts per moment.
    4. Fill. When slots remain, as in short episodes, more alternatives are allowed without
       the per-moment cap.

    An alternative must overlap exactly one moment and move its start or end by at least
    ``min_boundary_shift_ms`` relative to every overlapping chosen window, so near-duplicates
    never survive.

    Recall versus size: a smaller shortlist costs less to evaluate with an LLM, but it is more
    likely to drop the best cut of a moment.
    """
    ranked = sorted(pool, key=rank_key)
    limit = config.max_per_episode
    chosen: dict[str, PromoCandidate] = {}
    reason: dict[str, str] = {}
    root_of: dict[str, str] = {}
    alternatives: Counter[str] = Counter()

    def try_add(candidate: PromoCandidate, why: str, max_alternatives: int) -> bool:
        cid = candidate.candidate_id
        if cid in chosen or len(chosen) >= limit:
            return False
        overlapping = [c for c in chosen.values() if iou(c, candidate) >= config.max_iou]
        if not overlapping:
            root_of[cid] = cid
        else:
            roots = {root_of[c.candidate_id] for c in overlapping}
            if len(roots) != 1:
                return False
            root = roots.pop()
            if alternatives[root] >= max_alternatives:
                return False
            if (
                min(_boundary_shift(candidate, c) for c in overlapping)
                < config.min_boundary_shift_ms
            ):
                return False
            alternatives[root] += 1
            prefix = "fill_" if why == "fill" else ""
            root_of[cid], why = root, f"{prefix}alternative_cut_of:{root}"
        chosen[cid], reason[cid] = candidate, why
        return True

    for region in range(config.regions):
        in_region = (c for c in ranked if _region(c, duration_ms, config.regions) == region)
        any(try_add(c, f"best_in_region:{region + 1}/{config.regions}", 0) for c in in_region)
    episode_endings = (c for c in ranked if c.end_ms == duration_ms)
    any(try_add(c, "best_episode_ending", config.alternatives_per_moment) for c in episode_endings)

    distinct_limit = max(len(chosen), limit - config.reserved_alternative_slots)
    for candidate in ranked:
        if len(chosen) >= distinct_limit:
            break
        try_add(candidate, "distinct_moment", 0)
    for candidate in ranked:
        try_add(candidate, "distinct_moment", config.alternatives_per_moment)
    for candidate in ranked:
        try_add(candidate, "fill", limit)
    return sorted(
        (c.model_copy(update={"selection_reason": reason[cid]}) for cid, c in chosen.items()),
        key=rank_key,
    )


def _region(candidate: PromoCandidate, duration_ms: int, regions: int) -> int:
    midpoint = (candidate.start_ms + candidate.end_ms) // 2
    return min(regions - 1, midpoint * regions // max(1, duration_ms))


def _boundary_shift(a: PromoCandidate, b: PromoCandidate) -> int:
    return max(abs(a.start_ms - b.start_ms), abs(a.end_ms - b.end_ms))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def generate_pool(
    episode: Episode, config: CandidateConfig | None = None
) -> tuple[EpisodeIndex, list[Anchor], list[Anchor], int, list[PromoCandidate]]:
    """Build the full scored pool, for inspection. Most callers want ``generate_candidates``."""
    config = config or CandidateConfig()
    index = EpisodeIndex(episode)
    starts = find_start_anchors(index, config)
    ends = find_end_anchors(index, config)
    raw, windows = pool_windows(starts, ends, config)
    pool = [build_candidate(index, start, end, config) for start, end in windows]
    return index, starts, ends, raw, pool


def generate_candidates(
    episode: Episode, config: CandidateConfig | None = None
) -> EpisodeCandidates:
    """Discover, score and shortlist candidates for one episode. Validated before returning."""
    config = config or CandidateConfig()
    _, starts, ends, raw, pool = generate_pool(episode, config)
    shortlist = select_shortlist(pool, episode.duration_ms, config)
    problems = validate_candidates(episode, shortlist, config)
    if problems:
        raise CandidateValidationError("; ".join(problems[:10]))
    return EpisodeCandidates(
        episode_number=episode.episode_number,
        duration_ms=episode.duration_ms,
        start_anchor_count=len(starts),
        end_anchor_count=len(ends),
        raw_window_count=raw,
        pool_size=len(pool),
        shortlist=tuple(shortlist),
    )


def generate_pack_candidates(
    episodes: Iterable[Episode], config: CandidateConfig | None = None
) -> list[EpisodeCandidates]:
    config = config or CandidateConfig()
    return [generate_candidates(episode, config) for episode in episodes]


# ---------------------------------------------------------------------------
# Validation and statistics
# ---------------------------------------------------------------------------


def validate_candidates(
    episode: Episode, candidates: Iterable[PromoCandidate], config: CandidateConfig
) -> list[str]:
    """Check candidates against their episode. Returns readable problems with no script text."""
    index = EpisodeIndex(episode)
    known = {e.event_id for e in index.timeline}
    problems: list[str] = []
    seen: set[tuple[int, int, int]] = set()
    for c in candidates:
        cid = c.candidate_id
        key = (c.episode_number, c.start_ms, c.end_ms)
        if key in seen:
            problems.append(f"{cid}: duplicate window")
        seen.add(key)
        if c.episode_number != episode.episode_number:
            problems.append(f"{cid}: belongs to episode {c.episode_number}")
        if not config.min_duration_ms <= c.duration_ms <= config.max_duration_ms:
            problems.append(f"{cid}: duration {c.duration_ms} ms outside allowed range")
        if c.start_ms < 0 or c.end_ms > episode.duration_ms:
            problems.append(f"{cid}: outside episode timeline [0, {episode.duration_ms})")
        if FALLBACK not in c.start_anchors and c.start_ms not in index.boundary_times:
            problems.append(f"{cid}: start is not a source timing")
        if FALLBACK not in c.end_anchors and c.end_ms not in index.boundary_times:
            problems.append(f"{cid}: end is not a source timing")
        unknown = set(c.opening_event_ids + c.ending_event_ids) - known
        if unknown:
            problems.append(f"{cid}: unknown event ids {sorted(unknown)}")
            continue
        hook_end = min(c.end_ms, c.start_ms + config.hook_window_ms)
        expected_open = tuple(e.event_id for e in index.intersecting(c.start_ms, hook_end))
        tail_start = max(c.start_ms, c.end_ms - config.ending_window_ms)
        expected_end = tuple(e.event_id for e in index.intersecting(tail_start, c.end_ms))
        if c.opening_event_ids != expected_open or c.ending_event_ids != expected_end:
            problems.append(f"{cid}: evidence ids do not match the boundary windows")
    return problems


def summarize(results: list[EpisodeCandidates], config: CandidateConfig) -> dict[str, object]:
    """Aggregate structural statistics (no script text). Coverage is a structural proxy only."""
    shortlist = [c for r in results for c in r.shortlist]
    durations = sorted(c.duration_ms for c in shortlist)
    per_episode = sorted(len(r.shortlist) for r in results)
    coverage = {r.episode_number: _coverage(r) for r in results}
    regions = {r.episode_number: _regions_touched(r, config.regions) for r in results}
    keys = [(c.episode_number, c.start_ms, c.end_ms) for c in shortlist]
    return {
        "episodes": len(results),
        "raw_windows": sum(r.raw_window_count for r in results),
        "pool": sum(r.pool_size for r in results),
        "shortlisted": len(shortlist),
        "per_episode_min_median_max": _min_med_max(per_episode),
        "duration_ms_min_p25_median_p75_max": _quantiles(durations),
        "coverage_min_median_max": _min_med_max(sorted(coverage.values())),
        "episodes_with_uncovered_region": sorted(
            n for n, touched in regions.items() if touched < config.regions
        ),
        "scene_crossing": sum(c.signals.crosses_scene for c in shortlist),
        "dialogue_boundary_conflicts": sum(
            c.signals.dialogue_boundary_conflicts > 0 for c in shortlist
        ),
        "any_event_straddling_boundary": sum(c.signals.straddling_events > 0 for c in shortlist),
        "fallback_anchor_boundaries": sum(
            FALLBACK in c.start_anchors or FALLBACK in c.end_anchors for c in shortlist
        ),
        "duplicate_windows": len(keys) - len(set(keys)),
        "selection_reasons": dict(
            Counter((c.selection_reason or "").split(":")[0] for c in shortlist)
        ),
    }


def _coverage(result: EpisodeCandidates) -> float:
    spans = sorted((c.start_ms, c.end_ms) for c in result.shortlist)
    covered, cursor = 0, 0
    for lo, hi in spans:
        covered += max(0, hi - max(lo, cursor))
        cursor = max(cursor, hi)
    return round(covered / result.duration_ms, 4) if result.duration_ms else 0.0


def _regions_touched(result: EpisodeCandidates, regions: int) -> int:
    width = result.duration_ms / regions
    return sum(
        any(c.start_ms < (r + 1) * width and c.end_ms > r * width for c in result.shortlist)
        for r in range(regions)
    )


def _min_med_max(values: list[float] | list[int]) -> tuple[float, float, float]:
    return (values[0], median(values), values[-1]) if values else (0, 0, 0)


def _quantiles(values: list[int]) -> tuple[int, ...]:
    if not values:
        return ()
    pick = [0, len(values) // 4, len(values) // 2, (3 * len(values)) // 4, len(values) - 1]
    return tuple(values[i] for i in pick)
