"""Deterministic heuristic baseline: interpretable priorities, not promo-quality judgements.

The baseline sees only structure and a few weak lexical hints: punctuation, dialogue delivery
notes, and a short list of generic English word stems. It cannot tell whether a moment is
actually shocking, whether a question really goes unanswered, or whether a new viewer would
follow the story. Its job is to:

- order candidates so that pruning keeps plausible windows,
- give an offline demo that needs no API key,
- give the LLM evaluator (Phase 3) something to be compared against,
- act as an explicitly labelled fallback when model calls fail.

Every component is in [0, 1]. The priority is the weighted mean of the components, using
``CandidateConfig.weights``.
"""

import re

from framecue.schemas import (
    CandidateConfig,
    CandidateSignals,
    EventType,
    HeuristicAssessment,
    ScriptEvent,
)

METHOD = "heuristic_baseline_v1"

# Generic stems, deliberately short and show-agnostic. A match is a hint, never a claim.
_INTENSE_DELIVERY = re.compile(
    r"\b(scream|shout|yell|furious|rage|enrag|angr|shock|stun|gasp|horrif|terrif|panic|"
    r"desperat|sob|cry|cries|hysteri|threat|menac|venom|cruel|roar|snarl|trembl|outrag|"
    r"accus|defian|plead|beg)",
    re.IGNORECASE,
)
_PHYSICAL = re.compile(
    r"\b(slap|strik|struck|punch|shov|push|grab|seiz|fall|fell|collaps|knee|throw|threw|"
    r"slam|smash|shatter|blood|knife|blade|gun|sword|kiss|burst|chok|drag|fight|attack|hit)",
    re.IGNORECASE,
)
_REVEAL = re.compile(r"\b(reveal|realiz|recogni|truth|secret|identity|unmask|discover)", re.I)

STRONG_CUES = frozenset({"intense_delivery", "physical_action", "reveal_cue", "sound_fx"})
OPEN_LINE_CUES = frozenset({"question", "trailing_off"})


def event_cues(event: ScriptEvent) -> frozenset[str]:
    """Return cue category names for one event. Never returns script text."""
    cues: set[str] = set()
    if event.event_type is EventType.DIALOGUE:
        line = event.text.strip().strip('"“”').strip()
        if line.endswith("?"):
            cues.add("question")
        if "!" in line:
            cues.add("exclamation")
        if line.endswith(("...", "…", "—", "--")):
            cues.add("trailing_off")
        if any(_INTENSE_DELIVERY.search(note) for note in event.annotations):
            cues.add("intense_delivery")
    elif _PHYSICAL.search(event.text):
        cues.add("physical_action")
    if event.event_type is EventType.SOUND_FX:
        cues.add("sound_fx")
    if _REVEAL.search(event.text):
        cues.add("reveal_cue")
    return frozenset(cues)


def assess(
    signals: CandidateSignals, duration_ms: int, config: CandidateConfig
) -> HeuristicAssessment:
    """Score one window from its signals. Pure function; deterministic."""
    components = {
        "opening": _opening(signals),
        "ending": _ending(signals),
        "activity": _activity(signals, duration_ms),
        "boundary": _boundary(signals),
        "duration": duration_preference(duration_ms, config),
        "context": _context(signals),
    }
    weights = config.weights.model_dump()
    total = sum(weights.values())
    priority = sum(weights[name] * value for name, value in components.items()) / total
    return HeuristicAssessment(
        method=METHOD,
        priority=round(priority, 6),
        components={name: round(value, 4) for name, value in components.items()},
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _opening(s: CandidateSignals) -> float:
    """Something starts in the hook window, preferably punctuated or intense."""
    cues = set(s.opening_cues)
    score = 0.4 if s.opening_onsets else 0.0
    score += 0.3 if cues & STRONG_CUES else 0.0
    score += 0.3 if cues & {"question", "exclamation"} else 0.0
    return _clamp(score)


def _ending(s: CandidateSignals) -> float:
    """Proxy for an unresolved cut: an open final line, cutting right before someone answers,
    an intense final beat, or a structural break."""
    cues = set(s.ending_cues)
    score = 0.35 if cues & OPEN_LINE_CUES else (0.2 if "exclamation" in cues else 0.0)
    score += 0.3 if cues & {"cut_before_line", "episode_end"} else 0.0
    score += 0.2 if cues & STRONG_CUES else 0.0
    score += 0.15 if cues & {"scene_end", "episode_end"} else 0.0
    return _clamp(score)


def _activity(s: CandidateSignals, duration_ms: int) -> float:
    """Dense lines and beats score higher. A long quiet stretch (dead air) is penalised."""
    per_10s = 10_000 / duration_ms
    lines = min(1.0, s.dialogue_lines * per_10s / 2.0)
    beats = min(1.0, s.non_camera_events * per_10s / 3.0)
    quiet_penalty = min(1.0, max(0, s.max_quiet_ms - 4_000) / 8_000)
    return _clamp(0.5 * lines + 0.5 * beats - 0.3 * quiet_penalty)


def _boundary(s: CandidateSignals) -> float:
    """Clean cuts at both ends and few events sliced by the boundaries."""
    score = 0.35 * s.clean_start_cut + 0.35 * s.clean_end_cut
    score += 0.3 * (1 - min(1.0, s.straddling_events / 3))
    return _clamp(score * (0.5 if s.dialogue_boundary_conflicts else 1.0))


def duration_preference(duration_ms: int, config: CandidateConfig) -> float:
    """1.0 inside the preferred range, falling linearly to 0.5 at the hard limits."""
    lo, hi = config.preferred_min_ms, config.preferred_max_ms
    if lo <= duration_ms <= hi:
        return 1.0
    if duration_ms < lo:
        span = lo - config.min_duration_ms
        return 1.0 - 0.5 * ((lo - duration_ms) / span if span else 1.0)
    span = config.max_duration_ms - hi
    return 1.0 - 0.5 * ((duration_ms - hi) / span if span else 1.0)


def _context(s: CandidateSignals) -> float:
    """Very weak standalone proxy: two or more parties, several lines, and a start at a scene
    opening, where the setting is established."""
    score = 0.4 * (s.speakers >= 2) + 0.3 * (s.dialogue_lines >= 3)
    return _clamp(score + 0.3 * s.starts_at_scene_start)
