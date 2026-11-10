"""Two separate ranking views. AI scores and heuristic priorities are never compared.

**View "ai": AI-reviewed recommendations.**
- Contains only candidates with a valid, current-profile model evaluation.
- Order: weighted AI score (desc), then cliffhanger, hook, standalone, pacing (desc), then
  episode, start, end and id (asc). The tie-breakers stay inside the AI rubric, plus fixed
  positional keys; the heuristic is never used.
- The view is *provisional* whenever AI coverage is incomplete.

**View "coverage": full-show candidate suggestions.**
- Contains every shortlisted candidate from every episode.
- Order: heuristic priority (desc), then episode, start, end and id (asc).
- Marked as unverified. These rows carry no AI scores or explanations, even when an AI
  evaluation exists; that lives in the AI view and in the detail.

**Near-duplicate suppression (both views, applied in rank order).** A candidate is an
*alternative cut* of an earlier primary in the same episode when either:
- IoU >= 0.5, or
- the overlap covers >= 80 % of the shorter clip.

Alternatives get no rank, so they cannot crowd the top. They stay available through
``include_alternatives`` and on each primary's ``alternatives`` list.

No candidate is ever forced into a view's top results. Episode-ending clips, for example,
rank purely on their own score.

Ranks are positions in the full view. Filters select from it and keep the original rank.
"""

from dataclasses import dataclass, field

from promolens.candidates import EpisodeIndex, iou
from promolens.results import (
    AI_SCORE_SYSTEM,
    BASELINE_SCORE_SYSTEM,
    AIJudgement,
    AIStatus,
    BaselineInfo,
    EpisodeSummary,
    PromoDetail,
    PromoPage,
    RankedPromo,
    ScoreInfo,
    Segment,
    SourceEvents,
    StructuralFlags,
    Timeline,
    TimelineEvent,
    TimelineScene,
    View,
)
from promolens.schemas import PromoCandidate, PromoEvaluation, Severity
from promolens.timecode import format_timestamp

IOU_DUPLICATE = 0.5
CONTAINMENT_DUPLICATE = 0.8
TIMELINE_CONTEXT_MS = 10_000


@dataclass(frozen=True)
class ViewEntry:
    candidate_id: str
    rank: int | None
    alternative_of: str | None = None
    alternatives: tuple[str, ...] = ()


@dataclass
class Views:
    ai: list[ViewEntry]
    coverage: list[ViewEntry]
    ai_index: dict[str, ViewEntry] = field(default_factory=dict)
    coverage_index: dict[str, ViewEntry] = field(default_factory=dict)

    def entries(self, view: View) -> list[ViewEntry]:
        return self.ai if view == "ai" else self.coverage


@dataclass(frozen=True)
class PromoFilter:
    episodes: frozenset[int] | None = None
    min_duration_s: float | None = None
    max_duration_s: float | None = None
    min_score: float | None = None  # in the view's own scoring system
    statuses: frozenset[AIStatus] | None = None
    warnings_any: frozenset[str] | None = None  # AI warnings; un-reviewed rows have none
    exclude_warnings: frozenset[str] | None = None
    include_alternatives: bool = False


# ---------------------------------------------------------------------------
# Building the views
# ---------------------------------------------------------------------------


def ai_sort_key(e: PromoEvaluation, c: PromoCandidate) -> tuple:
    s = e.scores
    assert s is not None and e.weighted_score is not None
    return (
        -e.weighted_score,
        -s.cliffhanger,
        -s.hook,
        -s.standalone,
        -s.pacing,
        c.episode_number,
        c.start_ms,
        c.end_ms,
        c.candidate_id,
    )


def coverage_sort_key(c: PromoCandidate) -> tuple:
    return (-c.heuristic.priority, c.episode_number, c.start_ms, c.end_ms, c.candidate_id)


def near_duplicate(a: PromoCandidate, b: PromoCandidate) -> bool:
    if a.episode_number != b.episode_number:
        return False
    overlap = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    if overlap == 0:
        return False
    shorter = min(a.duration_ms, b.duration_ms)
    return iou(a, b) >= IOU_DUPLICATE or overlap / shorter >= CONTAINMENT_DUPLICATE


def suppress(ordered: list[PromoCandidate]) -> list[ViewEntry]:
    """Greedy, in rank order: the first of each near-duplicate group is the primary."""
    primaries: list[PromoCandidate] = []
    alternatives: dict[str, list[str]] = {}
    alternative_of: dict[str, str] = {}
    for c in ordered:
        owner = next((p for p in primaries if near_duplicate(p, c)), None)
        if owner is None:
            primaries.append(c)
            alternatives[c.candidate_id] = []
        else:
            alternatives[owner.candidate_id].append(c.candidate_id)
            alternative_of[c.candidate_id] = owner.candidate_id
    entries: list[ViewEntry] = []
    for rank, p in enumerate(primaries, start=1):
        entries.append(ViewEntry(p.candidate_id, rank, None, tuple(alternatives[p.candidate_id])))
        entries.extend(ViewEntry(a, None, p.candidate_id) for a in alternatives[p.candidate_id])
    return entries


def build_views(run) -> Views:
    ai_ordered = sorted(
        (run.by_id[cid] for cid in run.ai), key=lambda c: ai_sort_key(run.ai[c.candidate_id], c)
    )
    coverage_ordered = sorted(run.by_id.values(), key=coverage_sort_key)
    views = Views(ai=suppress(ai_ordered), coverage=suppress(coverage_ordered))
    views.ai_index = {e.candidate_id: e for e in views.ai}
    views.coverage_index = {e.candidate_id: e for e in views.coverage}
    return views


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def segments(c: PromoCandidate) -> tuple[Segment, ...]:
    return tuple(
        Segment(
            index=i,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            start_timestamp=format_timestamp(s.start_ms),
            end_timestamp=format_timestamp(s.end_ms),
            duration_ms=s.duration_ms,
        )
        for i, s in enumerate(c.segments)
    )


def structural(run, c: PromoCandidate) -> StructuralFlags:
    s = c.signals
    return StructuralFlags(
        crosses_scene=s.crosses_scene,
        starts_at_scene_start=s.starts_at_scene_start,
        ends_at_scene_end=s.ends_at_scene_end,
        ends_at_episode_end=c.end_ms == run.episodes[c.episode_number].duration_ms,
        clean_start_cut=s.clean_start_cut,
        clean_end_cut=s.clean_end_cut,
    )


def ai_judgement(e: PromoEvaluation) -> AIJudgement:
    s = e.scores
    assert s is not None and e.weighted_score is not None and e.rubric_weights is not None
    p = e.provenance
    return AIJudgement(
        weighted_score=e.weighted_score,
        hook_score=s.hook,
        cliffhanger_score=s.cliffhanger,
        standalone_score=s.standalone,
        pacing_score=s.pacing,
        rubric_weights=e.rubric_weights.model_dump(),
        hook_event_ids=e.hook_event_ids,
        cliffhanger_event_ids=e.cliffhanger_event_ids,
        hook_explanation=e.hook_explanation or "",
        cliffhanger_explanation=e.cliffhanger_explanation or "",
        why_it_works=e.why_it_works or "",
        weakness=e.weakness or "",
        warnings=tuple(w.value for w in e.warnings),
        model_requested=p.model_requested,
        model_reported=p.model_reported,
        provider=p.provider,
        prompt_version=p.prompt_version,
        cache_key=p.cache_key,
    )


def ai_label(run) -> str:
    total, done = len(run.by_id), len(run.ai)
    if done == total:
        return "AI-reviewed recommendation"
    return f"AI-reviewed recommendation (provisional: {done} of {total} candidates reviewed)"


COVERAGE_LABEL = "Unverified candidate suggestion (heuristic baseline, not AI-reviewed)"


def ranked_promo(run, entry: ViewEntry, view: View) -> RankedPromo:
    c = run.by_id[entry.candidate_id]
    status = run.status_of(c.candidate_id)
    base = {
        "view": view,
        "rank": entry.rank,
        "alternative_of": entry.alternative_of,
        "alternatives": entry.alternatives,
        "candidate_id": c.candidate_id,
        "episode_number": c.episode_number,
        "segments": segments(c),
        "start_ms": c.start_ms,
        "end_ms": c.end_ms,
        "start_timestamp": format_timestamp(c.start_ms),
        "end_timestamp": format_timestamp(c.end_ms),
        "duration_ms": c.duration_ms,
        "duration_seconds": round(c.duration_ms / 1000, 2),
        "evaluation_status": status,
        "ai_score_available": status is AIStatus.AI_EVALUATED,
        "structural": structural(run, c),
        "source_events": SourceEvents(
            opening_window=c.opening_event_ids, ending_window=c.ending_event_ids
        ),
    }
    if view == "ai":
        e = run.ai[c.candidate_id]
        return RankedPromo(
            **base,
            score=ScoreInfo(
                system=AI_SCORE_SYSTEM,
                value=e.weighted_score,
                scale_max=100,
                label="AI weighted rubric score (0-100); an editorial estimate",
            ),
            hook_event_ids=e.hook_event_ids,
            cliffhanger_event_ids=e.cliffhanger_event_ids,
            hook_explanation=e.hook_explanation,
            cliffhanger_explanation=e.cliffhanger_explanation,
            why_it_works=e.why_it_works,
            weakness=e.weakness,
            warnings=tuple(w.value for w in e.warnings),
            label=ai_label(run),
        )
    return RankedPromo(
        **base,
        score=ScoreInfo(
            system=BASELINE_SCORE_SYSTEM,
            value=c.heuristic.priority,
            scale_max=1,
            label="Heuristic priority (0-1); unverified, not an AI judgement",
        ),
        hook_event_ids=(),
        cliffhanger_event_ids=(),
        label=COVERAGE_LABEL,
    )


def score_in_view(run, c: PromoCandidate, view: View) -> float:
    if view == "ai":
        return run.ai[c.candidate_id].weighted_score  # type: ignore[return-value]
    return c.heuristic.priority


def matches(run, entry: ViewEntry, view: View, f: PromoFilter) -> bool:
    c = run.by_id[entry.candidate_id]
    if entry.rank is None and not f.include_alternatives:
        return False
    if f.episodes is not None and c.episode_number not in f.episodes:
        return False
    seconds = c.duration_ms / 1000
    if f.min_duration_s is not None and seconds < f.min_duration_s:
        return False
    if f.max_duration_s is not None and seconds > f.max_duration_s:
        return False
    if f.min_score is not None and score_in_view(run, c, view) < f.min_score:
        return False
    if f.statuses is not None and run.status_of(c.candidate_id) not in f.statuses:
        return False
    warnings = (
        {w.value for w in run.ai[c.candidate_id].warnings} if c.candidate_id in run.ai else set()
    )
    if f.warnings_any is not None and not (warnings & f.warnings_any):
        return False
    return not (f.exclude_warnings is not None and warnings & f.exclude_warnings)


def page(
    run, view: View, f: PromoFilter | None = None, offset: int = 0, limit: int = 50
) -> PromoPage:
    f = f or PromoFilter()
    selected = [e for e in run.views.entries(view) if matches(run, e, view, f)]
    provisional = view == "ai" and len(run.ai) < len(run.by_id)
    reason = None
    if provisional:
        covered = len({run.by_id[c].episode_number for c in run.ai})
        reason = (
            f"AI evaluated {len(run.ai)} of {len(run.by_id)} candidates in {covered} of "
            f"{len(run.episodes)} episodes; rankings can change as coverage grows."
        )
    return PromoPage(
        view=view,
        scoring_system=AI_SCORE_SYSTEM if view == "ai" else BASELINE_SCORE_SYSTEM,
        provisional=provisional,
        provisional_reason=reason,
        total=len(selected),
        offset=offset,
        limit=limit,
        items=tuple(ranked_promo(run, e, view) for e in selected[offset : offset + limit]),
    )


def detail(run, candidate_id: str) -> PromoDetail:
    c = run.by_id[candidate_id]
    episode = run.episodes[c.episode_number]
    e = run.ai.get(candidate_id)
    views = run.views
    ai_entry, cov_entry = views.ai_index.get(candidate_id), views.coverage_index[candidate_id]
    return PromoDetail(
        candidate_id=c.candidate_id,
        episode_number=c.episode_number,
        segments=segments(c),
        start_ms=c.start_ms,
        end_ms=c.end_ms,
        start_timestamp=format_timestamp(c.start_ms),
        end_timestamp=format_timestamp(c.end_ms),
        duration_ms=c.duration_ms,
        duration_seconds=round(c.duration_ms / 1000, 2),
        evaluation_status=run.status_of(candidate_id),
        ai=ai_judgement(e) if e else None,
        ai_failure_reason=run.failed.get(candidate_id),
        baseline=BaselineInfo(
            method=c.heuristic.method,
            priority=c.heuristic.priority,
            components=c.heuristic.components,
            start_anchors=c.start_anchors,
            end_anchors=c.end_anchors,
            selection_reason=c.selection_reason,
        ),
        ai_rank=ai_entry.rank if ai_entry else None,
        ai_alternative_of=ai_entry.alternative_of if ai_entry else None,
        coverage_rank=cov_entry.rank,
        coverage_alternative_of=cov_entry.alternative_of,
        structural=structural(run, c),
        source_events=SourceEvents(
            opening_window=c.opening_event_ids, ending_window=c.ending_event_ids
        ),
        timeline=timeline(run, episode, c, e),
    )


def timeline(run, episode, c: PromoCandidate, e: PromoEvaluation | None) -> Timeline:
    cfg = run.candidate_config
    start, end = c.start_ms, c.end_ms
    hook = (start, min(end, start + cfg.hook_window_ms))
    tail = (max(start, end - cfg.ending_window_ms), end)
    lo = max(0, start - TIMELINE_CONTEXT_MS)
    hi = min(episode.duration_ms, end + TIMELINE_CONTEXT_MS)
    hook_ids = set(e.hook_event_ids) if e else set()
    cliff_ids = set(e.cliffhanger_event_ids) if e else set()
    opening, ending = set(c.opening_event_ids), set(c.ending_event_ids)

    def inside(ev, a: int, b: int) -> bool:
        if ev.start_ms == ev.end_ms:
            return a <= ev.start_ms < b
        return ev.start_ms < b and ev.end_ms > a

    events = []
    for ev in EpisodeIndex(episode).intersecting(lo, hi):
        roles = [
            name
            for name, ids in (
                ("ai_hook_evidence", hook_ids),
                ("ai_cliffhanger_evidence", cliff_ids),
                ("opening_window", opening),
                ("ending_window", ending),
            )
            if ev.event_id in ids
        ]
        events.append(
            TimelineEvent(
                event_id=ev.event_id,
                event_type=ev.event_type.value,
                scene_number=ev.scene_number,
                start_ms=ev.start_ms,
                end_ms=ev.end_ms,
                start_timestamp=format_timestamp(ev.start_ms),
                end_timestamp=format_timestamp(ev.end_ms),
                speaker=ev.speaker,
                annotations=ev.annotations,
                text=ev.text,
                in_clip=inside(ev, start, end),
                in_hook_window=inside(ev, *hook),
                in_ending_window=inside(ev, *tail),
                cut_at_start=ev.start_ms < start < ev.end_ms,
                cut_at_end=ev.start_ms < end < ev.end_ms,
                roles=tuple(roles),
            )
        )
    scenes = tuple(
        TimelineScene(
            scene_number=s.scene_number, heading=s.heading, start_ms=s.start_ms, end_ms=s.end_ms
        )
        for s in episode.scenes
        if s.start_ms < hi and s.end_ms > lo
    )
    return Timeline(
        episode_number=episode.episode_number,
        episode_duration_ms=episode.duration_ms,
        window_start_ms=lo,
        window_end_ms=hi,
        clip_start_ms=start,
        clip_end_ms=end,
        hook_window=hook,
        ending_window=tail,
        scenes=scenes,
        events=tuple(events),
    )


def episode_summaries(run) -> list[EpisodeSummary]:
    views = run.views
    best_ai: dict[int, ViewEntry] = {}
    for entry in views.ai:
        n = run.by_id[entry.candidate_id].episode_number
        if entry.rank is not None and n not in best_ai:
            best_ai[n] = entry
    top_cov: dict[int, ViewEntry] = {}
    for entry in views.coverage:
        n = run.by_id[entry.candidate_id].episode_number
        if entry.rank is not None and n not in top_cov:
            top_cov[n] = entry
    warnings_by_source: dict[str, int] = {}
    for issue in run.parse.issues:
        if issue.severity is Severity.WARNING:
            warnings_by_source[issue.source] = warnings_by_source.get(issue.source, 0) + 1
    out = []
    for n, episode in sorted(run.episodes.items()):
        cands = run.candidates[n]
        ai = best_ai.get(n)
        cov = top_cov.get(n)
        out.append(
            EpisodeSummary(
                episode_number=n,
                source_name=episode.source_name,
                duration_ms=episode.duration_ms,
                duration_timestamp=format_timestamp(episode.duration_ms),
                scene_count=len(episode.scenes),
                event_count=len(episode.events),
                candidate_count=len(cands),
                ai_evaluated_count=sum(c.candidate_id in run.ai for c in cands),
                best_ai_candidate_id=ai.candidate_id if ai else None,
                best_ai_score=run.ai[ai.candidate_id].weighted_score if ai else None,
                best_ai_rank=ai.rank if ai else None,
                top_coverage_candidate_id=cov.candidate_id if cov else None,
                top_coverage_rank=cov.rank if cov else None,
                parse_warnings=warnings_by_source.get(episode.source_name, 0),
            )
        )
    return out
