"""Typed, immutable data models for parsed screenplays and parse diagnostics.

Times are integer milliseconds on the episode-relative timeline. Collections are tuples so that
models are effectively immutable; they serialize to JSON lists and round-trip through
``model_validate_json``.
"""

from collections import Counter
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator

EVENT_ID_PATTERN = r"^E\d{3,}-S\d{2,}-\d{4,}$"


class EventType(StrEnum):
    CAMERA = "CAMERA"
    DIALOGUE = "DIALOGUE"
    ACTION = "ACTION"
    SOUND_FX = "SOUND_FX"


class Severity(StrEnum):
    """ERROR blocks use of the episode; WARNING is a source anomaly kept as written;
    INFO records legitimate but notable structure (e.g. overlapping shots)."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParseIssue(_Frozen):
    """A structured diagnostic. Messages never quote screenplay text."""

    severity: Severity
    code: str
    message: str
    source: str
    line: PositiveInt | None = None
    episode_number: PositiveInt | None = None
    scene_number: NonNegativeInt | None = None
    event_id: str | None = None

    def __str__(self) -> str:
        location = self.source if self.line is None else f"{self.source}:{self.line}"
        target = f" [{self.event_id}]" if self.event_id else ""
        return f"{self.severity} {self.code} at {location}{target}: {self.message}"


class SourceLine(_Frozen):
    """A non-blank source line the parser did not recognize, preserved verbatim."""

    line: PositiveInt
    text: str


class ScriptEvent(_Frozen):
    """One timed block (camera shot, action, dialogue line or sound effect)."""

    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    episode_number: PositiveInt
    scene_number: PositiveInt
    source_index: NonNegativeInt = Field(description="Zero-based position in the episode file.")
    source_line: PositiveInt = Field(description="1-based line of the **LABEL:** line.")
    event_type: EventType
    label: str = Field(min_length=1, description="Original label exactly as written.")
    speaker: str | None = None
    annotations: tuple[str, ...] = Field(
        default=(), description="Parenthetical notes on a dialogue label, in source order."
    )
    text: str = Field(min_length=1)
    start_ms: NonNegativeInt
    end_ms: NonNegativeInt

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @model_validator(mode="after")
    def _check(self) -> "ScriptEvent":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must not be before start_ms")
        is_dialogue = self.event_type is EventType.DIALOGUE
        if is_dialogue and not self.speaker:
            raise ValueError("dialogue events require a speaker")
        if not is_dialogue and (self.speaker is not None or self.annotations):
            raise ValueError("only dialogue events carry a speaker or annotations")
        return self


class Scene(_Frozen):
    episode_number: PositiveInt
    scene_number: PositiveInt
    heading: str = Field(min_length=1)
    start_ms: NonNegativeInt
    end_ms: NonNegativeInt
    source_line: PositiveInt
    events: tuple[ScriptEvent, ...] = Field(description="Events in source order.")
    unparsed_lines: tuple[SourceLine, ...] = ()

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @model_validator(mode="after")
    def _check(self) -> "Scene":
        if self.end_ms <= self.start_ms:
            raise ValueError("scene end must be after scene start")
        for event in self.events:
            if (event.episode_number, event.scene_number) != (
                self.episode_number,
                self.scene_number,
            ):
                raise ValueError("event does not belong to this scene")
        return self


class Episode(_Frozen):
    episode_number: PositiveInt
    source_name: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str | None = Field(default=None, description="Numeric id from the filename.")
    logline: str | None = None
    continuity_notes: str | None = None
    extra_metadata: dict[str, str] = Field(default_factory=dict)
    scenes: tuple[Scene, ...] = Field(min_length=1)
    unparsed_lines: tuple[SourceLine, ...] = Field(
        default=(), description="Unrecognized lines outside any scene."
    )

    @property
    def duration_ms(self) -> int:
        return max(scene.end_ms for scene in self.scenes)

    @property
    def events(self) -> list[ScriptEvent]:
        """All events in original source order."""
        return [event for scene in self.scenes for event in scene.events]

    def timeline(self) -> list[ScriptEvent]:
        """All events in deterministic chronological order: (start_ms, source_index)."""
        return sorted(self.events, key=lambda event: (event.start_ms, event.source_index))

    @model_validator(mode="after")
    def _check(self) -> "Episode":
        numbers = [scene.scene_number for scene in self.scenes]
        if len(set(numbers)) != len(numbers):
            raise ValueError("scene numbers must be unique")
        ids = [event.event_id for event in self.events]
        if len(set(ids)) != len(ids):
            raise ValueError("event ids must be unique")
        if any(scene.episode_number != self.episode_number for scene in self.scenes):
            raise ValueError("scene does not belong to this episode")
        return self


class ScreenplayParseError(ValueError):
    """Raised by ``ParseResult.raise_for_errors`` when ERROR-level issues exist."""

    def __init__(self, issues: list[ParseIssue]) -> None:
        self.issues = issues
        shown = "\n".join(f"  {issue}" for issue in issues[:20])
        more = f"\n  ... and {len(issues) - 20} more" if len(issues) > 20 else ""
        super().__init__(f"{len(issues)} parse error(s):\n{shown}{more}")


class ParseResult(_Frozen):
    """Episodes that parsed cleanly, plus every diagnostic. Episodes with errors are excluded."""

    source: str
    episodes: tuple[Episode, ...]
    issues: tuple[ParseIssue, ...]

    @property
    def errors(self) -> list[ParseIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> "ParseResult":
        if self.errors:
            raise ScreenplayParseError(self.errors)
        return self

    def issue_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(f"{i.severity}:{i.code}" for i in self.issues).items()))

    def counts(self) -> dict[str, int]:
        events = [event for episode in self.episodes for event in episode.events]
        by_type = Counter(event.event_type for event in events)
        return {
            "episodes": len(self.episodes),
            "scenes": sum(len(episode.scenes) for episode in self.episodes),
            "events": len(events),
            **{f"events_{kind.value}": by_type.get(kind, 0) for kind in EventType},
        }


# ---------------------------------------------------------------------------
# Phase 2: promo candidates
#
# Clip times are half-open intervals [start_ms, end_ms) on the episode timeline. An event
# intersects a clip when event.start_ms < clip.end_ms and event.end_ms > clip.start_ms.
# A zero-length event at time t intersects when clip.start_ms <= t < clip.end_ms.
# ---------------------------------------------------------------------------


def candidate_id_for(episode_number: int, start_ms: int, end_ms: int) -> str:
    """Deterministic, human-readable id: exactly one id per (episode, start, end) window."""
    return f"C{episode_number:03d}-{start_ms:07d}-{end_ms:07d}"


class ClipSegment(_Frozen):
    """One continuous stretch of an episode, half-open [start_ms, end_ms)."""

    episode_number: PositiveInt
    start_ms: NonNegativeInt
    end_ms: NonNegativeInt

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @model_validator(mode="after")
    def _check(self) -> "ClipSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("segment end must be after its start")
        return self


class BaselineWeights(_Frozen):
    """Relative weights of the heuristic baseline components (normalized when combined)."""

    opening: float = Field(default=0.25, ge=0)
    ending: float = Field(default=0.25, ge=0)
    activity: float = Field(default=0.15, ge=0)
    boundary: float = Field(default=0.15, ge=0)
    duration: float = Field(default=0.10, ge=0)
    context: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "BaselineWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one baseline weight must be positive")
        return self


class CandidateConfig(_Frozen):
    """All tunables for candidate discovery and shortlisting. The defaults are engineering
    starting points, not validated optima."""

    min_duration_ms: PositiveInt = 20_000
    max_duration_ms: PositiveInt = 60_000
    preferred_min_ms: PositiveInt = 30_000
    preferred_max_ms: PositiveInt = 45_000
    hook_window_ms: PositiveInt = 3_000
    ending_window_ms: PositiveInt = 5_000
    context_window_ms: PositiveInt = 10_000
    fallback_step_ms: PositiveInt = 5_000
    cut_before_line_ms: NonNegativeInt = Field(
        default=1_500, description="A line starting this soon after the end is 'cut before'."
    )
    settle_after_line_ms: NonNegativeInt = Field(
        default=1_500, description="A shot end this soon after a line is a clean end anchor."
    )
    duration_bands: PositiveInt = Field(default=4, description="Pool: duration bands per start.")
    ends_per_band: PositiveInt = Field(default=2, description="Pool: best ends kept per band.")
    max_per_episode: PositiveInt = 16
    regions: PositiveInt = Field(default=3, description="Early/middle/late coverage buckets.")
    max_iou: float = Field(default=0.5, gt=0, le=1, description="IoU at which clips overlap.")
    alternatives_per_moment: NonNegativeInt = Field(
        default=2, description="Extra cuts allowed around one already-selected moment."
    )
    reserved_alternative_slots: NonNegativeInt = Field(
        default=4, description="Slots that distinct moments may not take from alternative cuts."
    )
    min_boundary_shift_ms: PositiveInt = Field(
        default=2_000, description="An alternative cut must move its start or end this far."
    )
    weights: BaselineWeights = BaselineWeights()

    @model_validator(mode="after")
    def _check(self) -> "CandidateConfig":
        if self.min_duration_ms > self.max_duration_ms:
            raise ValueError("min_duration_ms must not exceed max_duration_ms")
        preferred = (self.preferred_min_ms, self.preferred_max_ms)
        if not self.min_duration_ms <= preferred[0] <= preferred[1] <= self.max_duration_ms:
            raise ValueError("preferred range must lie inside the duration range")
        return self


class CandidateSignals(_Frozen):
    """Deterministic structural facts about a window. Cue names are categories (for example
    'question'), never script text. They are weak lexical hints, not story judgements."""

    opening_onsets: NonNegativeInt = Field(description="Non-camera events starting in hook window.")
    opening_cues: tuple[str, ...] = ()
    ending_cues: tuple[str, ...] = ()
    dialogue_lines: NonNegativeInt = Field(description="Dialogue lines wholly inside the clip.")
    speakers: NonNegativeInt = Field(description="Distinct speaker labels among those lines.")
    non_camera_events: NonNegativeInt = Field(description="Non-camera events wholly inside.")
    camera_cuts: NonNegativeInt = Field(description="Camera shots starting strictly inside.")
    max_quiet_ms: NonNegativeInt = Field(description="Longest stretch with no non-camera event.")
    scene_numbers: tuple[PositiveInt, ...]
    starts_at_scene_start: bool
    ends_at_scene_end: bool
    clean_start_cut: bool
    clean_end_cut: bool
    straddling_events: NonNegativeInt = Field(description="Events cut by the start or end.")
    dialogue_boundary_conflicts: NonNegativeInt = Field(description="Lines cut by start or end.")

    @property
    def crosses_scene(self) -> bool:
        return len(self.scene_numbers) > 1


class HeuristicAssessment(_Frozen):
    """A deterministic HEURISTIC PRIORITY, used to order candidates for review and as the
    offline fallback. It is not an experimentally validated promo-quality score, and it is
    never an AI judgement. LLM rubric scores will live in a separate model (Phase 3)."""

    method: str
    priority: float = Field(ge=0, le=1)
    components: dict[str, float]


class PromoCandidate(_Frozen):
    candidate_id: str
    episode_number: PositiveInt
    segments: tuple[ClipSegment, ...] = Field(
        min_length=1, max_length=1, description="v1 allows exactly one continuous segment."
    )
    start_ms: NonNegativeInt
    end_ms: NonNegativeInt
    duration_ms: PositiveInt
    start_anchors: tuple[str, ...] = Field(min_length=1, description="Why the start was chosen.")
    end_anchors: tuple[str, ...] = Field(min_length=1, description="Why the end was chosen.")
    opening_event_ids: tuple[str, ...] = Field(description="Events intersecting the hook window.")
    ending_event_ids: tuple[str, ...] = Field(description="Events intersecting the end window.")
    signals: CandidateSignals
    heuristic: HeuristicAssessment
    selection_reason: str | None = Field(default=None, description="Why it was shortlisted.")

    @model_validator(mode="after")
    def _check(self) -> "PromoCandidate":
        segments = self.segments
        if any(s.episode_number != self.episode_number for s in segments):
            raise ValueError("segments must belong to the candidate's episode")
        if any(a.end_ms > b.start_ms for a, b in zip(segments, segments[1:], strict=False)):
            raise ValueError("segments must be ordered and non-overlapping")
        if (self.start_ms, self.end_ms) != (segments[0].start_ms, segments[-1].end_ms):
            raise ValueError("start/end must match the segment bounds")
        if self.duration_ms != sum(s.duration_ms for s in segments):
            raise ValueError("duration must equal the summed segment durations")
        if self.candidate_id != candidate_id_for(self.episode_number, self.start_ms, self.end_ms):
            raise ValueError("candidate_id does not match episode/start/end")
        prefix = f"E{self.episode_number:03d}-"
        if any(not i.startswith(prefix) for i in self.opening_event_ids + self.ending_event_ids):
            raise ValueError("evidence event ids must belong to the candidate's episode")
        return self


class EpisodeCandidates(_Frozen):
    """The shortlist for one episode, plus the pool counts from before shortlisting."""

    episode_number: PositiveInt
    duration_ms: NonNegativeInt
    start_anchor_count: NonNegativeInt
    end_anchor_count: NonNegativeInt
    raw_window_count: NonNegativeInt = Field(description="All anchor pairs within the range.")
    pool_size: NonNegativeInt = Field(description="Windows kept after per-start pruning.")
    shortlist: tuple[PromoCandidate, ...]


# ---------------------------------------------------------------------------
# Phase 3: AI promo evaluation
#
# The model supplies only 0-5 integer judgements, evidence event ids and short explanations.
# Python validates every id against the parsed episode and computes the weighted 0-100 score.
# Scores are editorial estimates, not measured conversion probabilities.
# ---------------------------------------------------------------------------

SCORE_MAX = 5
EXPLANATION_MAX_CHARS = 300


class QualityWarning(StrEnum):
    NO_CLEAR_HOOK = "no_clear_hook"
    HOOK_TOO_LATE = "hook_too_late"
    WEAK_ENDING = "weak_ending"
    RESOLVED_BEFORE_CUT = "resolved_before_cut"
    NEEDS_PRIOR_CONTEXT = "needs_prior_context"
    UNCLEAR_RELATIONSHIPS = "unclear_relationships"
    SPOILER_INSIDE_CLIP = "spoiler_inside_clip"
    DEAD_AIR = "dead_air"
    STARTS_MID_ACTION = "starts_mid_action"
    ENDS_MID_ACTION = "ends_mid_action"


class RubricWeights(_Frozen):
    """Weights of the four editorial criteria. Normalized when combined."""

    hook: float = Field(default=0.30, ge=0)
    cliffhanger: float = Field(default=0.30, ge=0)
    standalone: float = Field(default=0.25, ge=0)
    pacing: float = Field(default=0.15, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "RubricWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one rubric weight must be positive")
        return self


class RubricScores(_Frozen):
    hook: int = Field(ge=0, le=SCORE_MAX)
    cliffhanger: int = Field(ge=0, le=SCORE_MAX)
    standalone: int = Field(ge=0, le=SCORE_MAX)
    pacing: int = Field(ge=0, le=SCORE_MAX)


def weighted_score(scores: RubricScores, weights: RubricWeights) -> float:
    """Deterministic 0-100 editorial score: the weighted mean of the 0-5 criteria, scaled."""
    w = weights.model_dump()
    total = sum(w[name] * getattr(scores, name) for name in w)
    return round(100 * total / (SCORE_MAX * sum(w.values())), 2)


Explanation = Annotated[str, Field(min_length=1, max_length=EXPLANATION_MAX_CHARS)]


class ModelCandidateAssessment(_Frozen):
    """Exactly what the model must return for one candidate (strictly validated)."""

    candidate_id: str
    hook_score: int = Field(ge=0, le=SCORE_MAX)
    cliffhanger_score: int = Field(ge=0, le=SCORE_MAX)
    standalone_score: int = Field(ge=0, le=SCORE_MAX)
    pacing_score: int = Field(ge=0, le=SCORE_MAX)
    hook_event_ids: tuple[str, ...]
    cliffhanger_event_ids: tuple[str, ...]
    hook_explanation: Explanation
    cliffhanger_explanation: Explanation
    why_it_works: Explanation
    weakness: Explanation
    warnings: tuple[QualityWarning, ...]

    def scores(self) -> RubricScores:
        return RubricScores(
            hook=self.hook_score,
            cliffhanger=self.cliffhanger_score,
            standalone=self.standalone_score,
            pacing=self.pacing_score,
        )


class EvaluationStatus(StrEnum):
    LLM_EVALUATED = "llm_evaluated"
    FAILED = "failed"
    HEURISTIC_FALLBACK = "heuristic_fallback"


class EvaluationProvenance(_Frozen):
    evaluator: Literal["llm", "heuristic_fallback", "none"]
    model_requested: str | None = None
    model_reported: str | None = None
    provider: str | None = Field(default=None, description="Serving provider, as reported.")
    prompt_version: str | None = None
    attempts: NonNegativeInt = Field(default=0, description="Model requests this item was in.")
    from_cache: bool = False
    cache_key: str | None = None


class PromoEvaluation(_Frozen):
    """The result for one candidate. Only status LLM_EVALUATED carries AI rubric scores.

    A heuristic fallback is labelled as such and has no rubric scores. Its
    ``heuristic_priority`` is the Phase 2 baseline and is never presented as an AI judgement.
    """

    candidate_id: str
    episode_number: PositiveInt
    start_ms: NonNegativeInt
    end_ms: NonNegativeInt
    status: EvaluationStatus
    scores: RubricScores | None = None
    weighted_score: float | None = Field(default=None, ge=0, le=100)
    rubric_weights: RubricWeights | None = None
    hook_event_ids: tuple[str, ...] = ()
    cliffhanger_event_ids: tuple[str, ...] = ()
    hook_explanation: str | None = None
    cliffhanger_explanation: str | None = None
    why_it_works: str | None = None
    weakness: str | None = None
    warnings: tuple[QualityWarning, ...] = ()
    heuristic_priority: float = Field(ge=0, le=1, description="Phase 2 heuristic; not an AI score")
    failure_reason: str | None = None
    provenance: EvaluationProvenance

    @model_validator(mode="after")
    def _check(self) -> "PromoEvaluation":
        llm = self.status is EvaluationStatus.LLM_EVALUATED
        has_scores = self.scores is not None and self.weighted_score is not None
        if llm and (not has_scores or self.failure_reason or self.provenance.evaluator != "llm"):
            raise ValueError("an LLM evaluation needs scores, no failure, and llm provenance")
        if not llm and (self.scores is not None or self.weighted_score is not None):
            raise ValueError("failed or fallback results must not carry AI rubric scores")
        if not llm and not self.failure_reason:
            raise ValueError("failed or fallback results must state why")
        fallback = self.status is EvaluationStatus.HEURISTIC_FALLBACK
        if fallback != (self.provenance.evaluator == "heuristic_fallback"):
            raise ValueError("fallback status and provenance must agree")
        return self


class EvaluationRunStats(_Frozen):
    """Measured usage. Reported cost comes from the provider; estimates are labelled."""

    requests: NonNegativeInt = 0
    successful_responses: NonNegativeInt = 0
    failed_requests: NonNegativeInt = 0
    http_retries: NonNegativeInt = 0
    prompt_tokens: NonNegativeInt = 0
    completion_tokens: NonNegativeInt = 0
    reported_cost: float | None = Field(default=None, description="Sum of provider-reported cost.")
    responses_with_reported_cost: NonNegativeInt = 0
    estimated_cost_usd: float | None = Field(
        default=None, description="ESTIMATE from configured per-token prices, if set."
    )
    latency_ms_total: NonNegativeInt = 0
    cache_hits: NonNegativeInt = 0
    cache_misses: NonNegativeInt = 0
    evaluated: NonNegativeInt = 0
    failed: NonNegativeInt = 0
    fallback: NonNegativeInt = 0
    models_reported: tuple[str, ...] = ()
    providers_reported: tuple[str, ...] = ()


class EvaluationRun(_Frozen):
    evaluations: tuple[PromoEvaluation, ...]
    stats: EvaluationRunStats
    prompt_version: str
    model_requested: str | None
    aborted_reason: str | None = Field(
        default=None,
        description="Set when a run-level error (auth, credits, routing) stopped calls.",
    )
