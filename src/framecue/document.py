"""The self-contained analysis document returned by the stateless ``POST /api/analyze``.

A browser can hold one document in memory and do everything without another request:
filter, rank, inspect timelines and export. It is normalized:

- ``events``: every screenplay event exactly once, sorted by (episode, start, source order).
  Each episode records its ``event_range`` [first, last) into this list, so a timeline is a
  slice plus a time-window filter.
- ``candidates``: every shortlisted candidate, with timestamps, the baseline, structural
  flags, structural source-event ids, the evaluation status and ranks in both views. No
  screenplay text is repeated here.
- ``ai_evaluations``: genuine, validated model judgements keyed by candidate id. Candidates
  without one simply have no entry; nothing is synthesized.
- ``views``: the two rankings as ordered id lists. They are never merged.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from framecue.pipeline import RunResult, coverage, run_summary
from framecue.ranking import COVERAGE_LABEL, ai_judgement, ai_label, segments, structural
from framecue.results import (
    AI_SCORE_SYSTEM,
    BASELINE_SCORE_SYSTEM,
    AIJudgement,
    AIStatus,
    BaselineInfo,
    EvaluationCoverage,
    InputInfo,
    ParseSummary,
    Segment,
    SourceEvents,
    StructuralFlags,
)
from framecue.timecode import format_timestamp

SCHEMA_VERSION = "framecue-analysis-v1"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocEvent(_Model):
    id: str
    episode: int
    scene: int
    type: str
    start_ms: int
    end_ms: int
    speaker: str | None
    annotations: tuple[str, ...]
    text: str


class DocScene(_Model):
    scene_number: int
    heading: str
    start_ms: int
    end_ms: int


class DocEpisode(_Model):
    episode_number: int
    source_name: str
    duration_ms: int
    duration_timestamp: str
    logline: str | None
    continuity_notes: str | None
    scenes: tuple[DocScene, ...]
    event_range: tuple[int, int] = Field(description="[first, last) indices into events.")
    candidate_count: int
    ai_evaluated_count: int
    best_ai_candidate_id: str | None
    top_coverage_candidate_id: str | None
    parse_warnings: int


class DocCandidate(_Model):
    candidate_id: str
    episode_number: int
    segments: tuple[Segment, ...]
    start_ms: int
    end_ms: int
    start_timestamp: str
    end_timestamp: str
    duration_ms: int
    evaluation_status: AIStatus
    ai_failure_reason: str | None
    baseline: BaselineInfo
    structural: StructuralFlags
    source_events: SourceEvents
    ai_rank: int | None
    ai_alternative_of: str | None
    coverage_rank: int | None
    coverage_alternative_of: str | None


class DocView(_Model):
    view: Literal["ai", "coverage"]
    scoring_system: str
    score_scale_max: float
    label: str
    provisional: bool
    provisional_reason: str | None
    primaries: int
    order: tuple[str, ...] = Field(
        description="All members in display order: each primary followed by its alternatives."
    )


class DocViews(_Model):
    ai: DocView
    coverage: DocView


class DocSummary(_Model):
    analyzed_at: str
    input: InputInfo
    episodes: int
    scenes: int
    events: int
    candidates: int
    parse: ParseSummary
    timings_ms: dict[str, int]
    versions: dict[str, str]
    live_ai_calls: int
    ai_source: Literal["local_cache", "uploaded_bundle", "none"]


class AnalysisDocument(_Model):
    schema_version: Literal["framecue-analysis-v1"] = SCHEMA_VERSION
    summary: DocSummary
    coverage: EvaluationCoverage
    episodes: tuple[DocEpisode, ...]
    events: tuple[DocEvent, ...]
    candidates: tuple[DocCandidate, ...]
    ai_evaluations: dict[str, AIJudgement]
    views: DocViews
    notes: tuple[str, ...]


def build_document(run: RunResult) -> AnalysisDocument:
    views = run.views
    summary = run_summary(run)
    cov = coverage(run)

    events: list[DocEvent] = []
    ranges: dict[int, tuple[int, int]] = {}
    for n, episode in sorted(run.episodes.items()):
        first = len(events)
        for ev in episode.timeline():
            events.append(
                DocEvent(
                    id=ev.event_id,
                    episode=n,
                    scene=ev.scene_number,
                    type=ev.event_type.value,
                    start_ms=ev.start_ms,
                    end_ms=ev.end_ms,
                    speaker=ev.speaker,
                    annotations=ev.annotations,
                    text=ev.text,
                )
            )
        ranges[n] = (first, len(events))

    best_ai: dict[int, str] = {}
    for e in views.ai:
        n = run.by_id[e.candidate_id].episode_number
        if e.rank is not None:
            best_ai.setdefault(n, e.candidate_id)
    top_cov: dict[int, str] = {}
    for e in views.coverage:
        if e.rank is not None:
            top_cov.setdefault(run.by_id[e.candidate_id].episode_number, e.candidate_id)
    warnings: dict[str, int] = {}
    for issue in run.parse.issues:
        if issue.severity.value == "WARNING":
            warnings[issue.source] = warnings.get(issue.source, 0) + 1

    episodes = tuple(
        DocEpisode(
            episode_number=n,
            source_name=ep.source_name,
            duration_ms=ep.duration_ms,
            duration_timestamp=format_timestamp(ep.duration_ms),
            logline=ep.logline,
            continuity_notes=ep.continuity_notes,
            scenes=tuple(
                DocScene(
                    scene_number=s.scene_number,
                    heading=s.heading,
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                )
                for s in ep.scenes
            ),
            event_range=ranges[n],
            candidate_count=len(run.candidates[n]),
            ai_evaluated_count=sum(c.candidate_id in run.ai for c in run.candidates[n]),
            best_ai_candidate_id=best_ai.get(n),
            top_coverage_candidate_id=top_cov.get(n),
            parse_warnings=warnings.get(ep.source_name, 0),
        )
        for n, ep in sorted(run.episodes.items())
    )

    ordered = sorted(run.by_id.values(), key=lambda c: (c.episode_number, c.start_ms, c.end_ms))
    candidates = []
    for c in ordered:
        ai_entry = views.ai_index.get(c.candidate_id)
        cov_entry = views.coverage_index[c.candidate_id]
        candidates.append(
            DocCandidate(
                candidate_id=c.candidate_id,
                episode_number=c.episode_number,
                segments=segments(c),
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                start_timestamp=format_timestamp(c.start_ms),
                end_timestamp=format_timestamp(c.end_ms),
                duration_ms=c.duration_ms,
                evaluation_status=run.status_of(c.candidate_id),
                ai_failure_reason=run.failed.get(c.candidate_id),
                baseline=BaselineInfo(
                    method=c.heuristic.method,
                    priority=c.heuristic.priority,
                    components=c.heuristic.components,
                    start_anchors=c.start_anchors,
                    end_anchors=c.end_anchors,
                    selection_reason=c.selection_reason,
                ),
                structural=structural(run, c),
                source_events=SourceEvents(
                    opening_window=c.opening_event_ids, ending_window=c.ending_event_ids
                ),
                ai_rank=ai_entry.rank if ai_entry else None,
                ai_alternative_of=ai_entry.alternative_of if ai_entry else None,
                coverage_rank=cov_entry.rank,
                coverage_alternative_of=cov_entry.alternative_of,
            )
        )

    provisional = len(run.ai) < len(run.by_id)
    reason = (
        f"AI evaluated {cov.ai_evaluated} of {cov.total_candidates} candidates in "
        f"{cov.episodes_with_ai} of {cov.total_episodes} episodes; rankings can change as "
        "coverage grows."
        if provisional
        else None
    )
    doc_views = DocViews(
        ai=DocView(
            view="ai",
            scoring_system=AI_SCORE_SYSTEM,
            score_scale_max=100,
            label=ai_label(run),
            provisional=provisional,
            provisional_reason=reason,
            primaries=sum(e.rank is not None for e in views.ai),
            order=tuple(e.candidate_id for e in views.ai),
        ),
        coverage=DocView(
            view="coverage",
            scoring_system=BASELINE_SCORE_SYSTEM,
            score_scale_max=1,
            label=COVERAGE_LABEL,
            provisional=False,
            provisional_reason=None,
            primaries=sum(e.rank is not None for e in views.coverage),
            order=tuple(e.candidate_id for e in views.coverage),
        ),
    )
    notes = (
        "AI scores (ai_weighted_v1, 0-100) and heuristic priorities (heuristic_priority_v1, 0-1) "
        "are separate scoring systems; never rank them together.",
        "Only candidates listed in ai_evaluations have AI judgements; nothing is synthesized.",
        "This analysis made no model API calls. The uploaded file is processed by the hosting "
        "platform for this request and is not stored by FrameCue.",
        "Times are milliseconds from episode start; clips are half-open [start, end).",
    )
    return AnalysisDocument(
        summary=DocSummary(
            analyzed_at=run.created_at,
            input=summary.input,
            episodes=summary.episodes,
            scenes=summary.scenes,
            events=summary.events,
            candidates=summary.candidates,
            parse=summary.parse,
            timings_ms=summary.timings_ms,
            versions=summary.versions,
            live_ai_calls=summary.live_ai_calls,
            ai_source=cov.cache.source,
        ),
        coverage=cov,
        episodes=episodes,
        events=tuple(events),
        candidates=tuple(candidates),
        ai_evaluations={cid: ai_judgement(run.ai[cid]) for cid in sorted(run.ai)},
        views=doc_views,
        notes=notes,
    )
