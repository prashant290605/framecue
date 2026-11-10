"""Typed result models shared by the pipeline, the exports and the HTTP API.

These are the contract for the React frontend. Two scoring systems exist, and they are
never mixed:

- ``ai_weighted_v1``: the 0-100 weighted rubric score from a real, validated model
  evaluation. Only candidates with ``evaluation_status == "ai_evaluated"`` carry one.
- ``heuristic_priority_v1``: the 0-1 deterministic baseline priority. It is an unverified
  ordering signal, not an editorial judgement.

Every promo exposes exact millisecond times plus ``MM:SS.cc`` timestamps for each ordered
segment. Model explanations appear only where a real model produced them.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AI_SCORE_SYSTEM = "ai_weighted_v1"
BASELINE_SCORE_SYSTEM = "heuristic_priority_v1"

View = Literal["ai", "coverage"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AIStatus(StrEnum):
    AI_EVALUATED = "ai_evaluated"  # a valid, current-profile model evaluation exists
    NOT_EVALUATED = "not_evaluated"  # baseline only; never sent to (or accepted from) a model
    FAILED = "failed"  # an opt-in live run tried and could not obtain a valid evaluation


class Segment(_Model):
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    start_timestamp: str
    end_timestamp: str
    duration_ms: int = Field(gt=0)


class ScoreInfo(_Model):
    system: Literal["ai_weighted_v1", "heuristic_priority_v1"]
    value: float
    scale_max: float
    label: str


class StructuralFlags(_Model):
    """Deterministic facts about the window's boundaries. Not quality judgements."""

    crosses_scene: bool
    starts_at_scene_start: bool
    ends_at_scene_end: bool
    ends_at_episode_end: bool
    clean_start_cut: bool
    clean_end_cut: bool


class SourceEvents(_Model):
    """Structural event references: events intersecting the first 3 s and the last 5 s.
    These are windows computed by code, not model evidence."""

    opening_window: tuple[str, ...]
    ending_window: tuple[str, ...]


class AIJudgement(_Model):
    """A genuine, validated model evaluation. Every id here was checked against the parsed
    episode by interval intersection."""

    weighted_score: float = Field(ge=0, le=100)
    hook_score: int = Field(ge=0, le=5)
    cliffhanger_score: int = Field(ge=0, le=5)
    standalone_score: int = Field(ge=0, le=5)
    pacing_score: int = Field(ge=0, le=5)
    rubric_weights: dict[str, float]
    hook_event_ids: tuple[str, ...]
    cliffhanger_event_ids: tuple[str, ...]
    hook_explanation: str
    cliffhanger_explanation: str
    why_it_works: str
    weakness: str
    warnings: tuple[str, ...]
    model_requested: str | None
    model_reported: str | None
    provider: str | None
    prompt_version: str | None
    cache_key: str | None


class BaselineInfo(_Model):
    method: str
    priority: float = Field(ge=0, le=1)
    components: dict[str, float]
    start_anchors: tuple[str, ...]
    end_anchors: tuple[str, ...]
    selection_reason: str | None


class RankedPromo(_Model):
    view: View
    rank: int | None = Field(description="1-based rank among primaries; None for alternatives.")
    alternative_of: str | None = Field(
        default=None, description="Primary candidate this near-duplicate cut belongs to."
    )
    alternatives: tuple[str, ...] = Field(default=(), description="Near-duplicate cuts.")
    candidate_id: str
    episode_number: int
    segments: tuple[Segment, ...]
    start_ms: int
    end_ms: int
    start_timestamp: str
    end_timestamp: str
    duration_ms: int
    duration_seconds: float
    score: ScoreInfo
    evaluation_status: AIStatus
    ai_score_available: bool
    hook_event_ids: tuple[str, ...] = Field(description="AI evidence; empty without AI review.")
    cliffhanger_event_ids: tuple[str, ...] = Field(description="AI evidence; empty otherwise.")
    hook_explanation: str | None = None
    cliffhanger_explanation: str | None = None
    why_it_works: str | None = None
    weakness: str | None = None
    warnings: tuple[str, ...] = Field(default=(), description="AI quality warnings.")
    structural: StructuralFlags
    source_events: SourceEvents
    label: str


class TimelineEvent(_Model):
    event_id: str
    event_type: str
    scene_number: int
    start_ms: int
    end_ms: int
    start_timestamp: str
    end_timestamp: str
    speaker: str | None
    annotations: tuple[str, ...]
    text: str
    in_clip: bool
    in_hook_window: bool
    in_ending_window: bool
    cut_at_start: bool
    cut_at_end: bool
    roles: tuple[str, ...] = Field(
        description="ai_hook_evidence, ai_cliffhanger_evidence, opening_window, ending_window"
    )


class TimelineScene(_Model):
    scene_number: int
    heading: str
    start_ms: int
    end_ms: int


class Timeline(_Model):
    episode_number: int
    episode_duration_ms: int
    window_start_ms: int
    window_end_ms: int
    clip_start_ms: int
    clip_end_ms: int
    hook_window: tuple[int, int]
    ending_window: tuple[int, int]
    scenes: tuple[TimelineScene, ...]
    events: tuple[TimelineEvent, ...]


class PromoDetail(_Model):
    candidate_id: str
    episode_number: int
    segments: tuple[Segment, ...]
    start_ms: int
    end_ms: int
    start_timestamp: str
    end_timestamp: str
    duration_ms: int
    duration_seconds: float
    evaluation_status: AIStatus
    ai: AIJudgement | None = Field(description="Present only for a genuine model evaluation.")
    ai_failure_reason: str | None = None
    baseline: BaselineInfo
    ai_rank: int | None
    ai_alternative_of: str | None
    coverage_rank: int | None
    coverage_alternative_of: str | None
    structural: StructuralFlags
    source_events: SourceEvents
    timeline: Timeline


class PromoPage(_Model):
    view: View
    scoring_system: str
    provisional: bool
    provisional_reason: str | None
    total: int = Field(description="Items matching the filters.")
    offset: int
    limit: int
    items: tuple[RankedPromo, ...]


class EpisodeSummary(_Model):
    episode_number: int
    source_name: str
    duration_ms: int
    duration_timestamp: str
    scene_count: int
    event_count: int
    candidate_count: int
    ai_evaluated_count: int
    best_ai_candidate_id: str | None
    best_ai_score: float | None
    best_ai_rank: int | None
    top_coverage_candidate_id: str | None
    top_coverage_rank: int | None
    parse_warnings: int


class CacheAudit(_Model):
    """What was found in the AI cache and why entries were or were not used."""

    cache_dir_present: bool
    source: Literal["local_cache", "uploaded_bundle", "none"] = Field(
        default="none", description="Where AI evaluations were looked up."
    )
    entries_scanned: int
    used: int
    invalid_on_revalidation: tuple[str, ...] = Field(description="Cache keys rejected.")
    stale_prompt_version: int
    other_profile_or_input: int
    not_in_this_input: int
    stale_prompt_versions_seen: tuple[str, ...]


class AIProfile(_Model):
    provider: str
    model: str
    reasoning_effort: str | None
    prompt_version: str
    scoring_rules: str


class EvaluationCoverage(_Model):
    total_episodes: int
    total_candidates: int
    ai_evaluated: int
    not_evaluated: int
    failed: int
    episodes_with_ai: int
    episodes_without_ai: tuple[int, ...]
    candidate_coverage: float = Field(ge=0, le=1)
    episode_coverage: float = Field(ge=0, le=1)
    complete: bool
    profile: AIProfile
    cache: CacheAudit


class InputInfo(_Model):
    name: str
    kind: Literal["zip", "txt", "directory", "files"]
    sha256: str
    size_bytes: int
    files: int


class ParseSummary(_Model):
    errors: int
    warnings: int
    info: int
    excluded_sources: tuple[str, ...]
    issue_codes: dict[str, int]


class RunSummary(_Model):
    run_id: str
    status: Literal["completed", "failed"]
    created_at: str
    input: InputInfo
    episodes: int
    scenes: int
    events: int
    candidates: int
    parse: ParseSummary
    coverage: EvaluationCoverage
    timings_ms: dict[str, int]
    versions: dict[str, str]
    live_ai_calls: int = Field(description="Model API calls made by this run (offline: 0).")


class ExportFile(_Model):
    name: str
    description: str
    records: int
    sha256: str
    bytes: int


class ExportManifest(_Model):
    run: RunSummary
    generated_at: str
    files: tuple[ExportFile, ...]
    raw_model_outputs: str = Field(description="Availability of unmodified model output.")
    notes: tuple[str, ...]
