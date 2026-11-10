"""Offline full-pack orchestration: parse -> candidates -> baseline -> cached AI lookup.

``analyze_path`` and ``analyze_parsed`` never call a model API. AI assessments are reused
only when a cache entry's key matches exactly. The key covers the episode file checksum, the
candidate window, the prompt version, the scoring rules, the inference profile and the
evidence windows. Every reused entry is also re-validated against the parsed episode. All
other cache entries are counted in a ``CacheAudit`` and excluded.

Live evaluation is a separate, explicit opt-in path (``promolens evaluate`` /
``promolens.eval_driver --live``). Its results reach this pipeline only through the cache.
"""

import hashlib
import secrets
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from promolens import __version__
from promolens.baseline import METHOD as BASELINE_METHOD
from promolens.candidates import generate_candidates
from promolens.evaluator import (
    SCORING_RULES_VERSION,
    EvaluationCache,
    MemoryCache,
    cache_key,
    load_cached_evaluation,
)
from promolens.llm_client import LLMSettings
from promolens.parser import parse_path
from promolens.prompts import PROMPT_VERSION
from promolens.results import (
    AIProfile,
    AIStatus,
    CacheAudit,
    EvaluationCoverage,
    InputInfo,
    ParseSummary,
    RunSummary,
)
from promolens.schemas import (
    CandidateConfig,
    Episode,
    ParseResult,
    PromoCandidate,
    PromoEvaluation,
    RubricWeights,
    Severity,
)

RANKING_RULES_VERSION = "ranking-v1"
DEFAULT_CACHE_DIR = Path("runs/cache/evaluations")


def default_ai_profile() -> LLMSettings:
    """The inference profile that produced the project's cached assessments: GroqCloud
    free plan, ``openai/gpt-oss-120b``, low reasoning. No key is needed for lookups."""
    return LLMSettings(provider="groq")


class NoEpisodesError(ValueError):
    """The input produced no valid episode. ``codes`` are sanitized issue codes."""

    def __init__(self, codes: dict[str, int]) -> None:
        super().__init__(f"no valid episodes; issue codes: {codes}")
        self.codes = codes


@dataclass
class RunResult:
    run_id: str
    created_at: str
    input: InputInfo
    parse: ParseResult
    episodes: dict[int, Episode]
    candidates: dict[int, list[PromoCandidate]]  # per episode, in shortlist order
    by_id: dict[str, PromoCandidate]
    ai: dict[str, PromoEvaluation]  # valid, current-profile evaluations only
    failed: dict[str, str]  # candidate id -> reason recorded by an opt-in live run
    audit: CacheAudit
    profile: AIProfile
    timings_ms: dict[str, int]
    candidate_config: CandidateConfig
    rubric: RubricWeights
    live_ai_calls: int = 0
    _views: object = field(default=None, repr=False)

    def status_of(self, candidate_id: str) -> AIStatus:
        if candidate_id in self.ai:
            return AIStatus.AI_EVALUATED
        if candidate_id in self.failed:
            return AIStatus.FAILED
        return AIStatus.NOT_EVALUATED

    @property
    def views(self):  # -> ranking.Views (imported lazily to avoid a cycle)
        if self._views is None:
            from promolens.ranking import build_views

            self._views = build_views(self)
        return self._views


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def analyze_path(path: str | Path, **kwargs) -> RunResult:
    """Analyze a ZIP, a directory of ``.txt`` files, or one ``.txt`` file, offline."""
    path = Path(path)
    started = time.perf_counter()
    parsed = parse_path(path)
    parse_ms = _ms(started)
    return analyze_parsed(parsed, describe_path(path), parse_ms=parse_ms, **kwargs)


def analyze_parsed(
    parsed: ParseResult,
    input_info: InputInfo,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    cache: object | None = None,
    ai_settings: LLMSettings | None = None,
    candidate_config: CandidateConfig | None = None,
    rubric: RubricWeights | None = None,
    run_id: str | None = None,
    parse_ms: int = 0,
) -> RunResult:
    """Build a run from already parsed episodes. Raises ``NoEpisodesError`` when nothing
    usable was parsed. Never makes network calls."""
    total_started = time.perf_counter() - parse_ms / 1000
    if not parsed.episodes:
        raise NoEpisodesError(dict(Counter(i.code for i in parsed.issues)))
    settings = ai_settings or default_ai_profile()
    config = candidate_config or CandidateConfig()
    weights = rubric or RubricWeights()

    started = time.perf_counter()
    episodes = {e.episode_number: e for e in parsed.episodes}
    candidates = {
        n: list(generate_candidates(e, config).shortlist) for n, e in sorted(episodes.items())
    }
    by_id = {c.candidate_id: c for cs in candidates.values() for c in cs}
    candidates_ms = _ms(started)

    started = time.perf_counter()
    if cache is None and cache_dir is not None:
        cache = EvaluationCache(cache_dir)
    ai, failed, audit = _lookup(cache, episodes, candidates, settings, config, weights)
    lookup_ms = _ms(started)

    return RunResult(
        run_id=run_id or new_run_id(),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        input=input_info,
        parse=parsed,
        episodes=episodes,
        candidates=candidates,
        by_id=by_id,
        ai=ai,
        failed=failed,
        audit=audit,
        profile=AIProfile(
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            prompt_version=PROMPT_VERSION,
            scoring_rules=SCORING_RULES_VERSION,
        ),
        timings_ms={
            "parse": parse_ms,
            "candidates": candidates_ms,
            "ai_cache_lookup": lookup_ms,
            "total": _ms(total_started),
        },
        candidate_config=config,
        rubric=weights,
    )


def new_run_id() -> str:
    """Unguessable: 128 bits from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Cache lookup and audit
# ---------------------------------------------------------------------------


def _lookup(cache, episodes, candidates, settings, config, weights):
    ai: dict[str, PromoEvaluation] = {}
    failed: dict[str, str] = {}
    rejected: list[str] = []
    expected: set[str] = set()
    if cache is None:
        return ai, failed, _audit(False, 0, 0, rejected, Counter(), 0, 0, "none")
    for number, cands in candidates.items():
        episode = episodes[number]
        for c in cands:
            key = cache_key(episode, c, settings, config)
            expected.add(key)
            evaluation, was_rejected = load_cached_evaluation(
                cache, key, episode, c, config, weights
            )
            if evaluation is not None:
                ai[c.candidate_id] = evaluation
                continue
            if was_rejected:
                rejected.append(key)
            failure = cache.get_failure(key)
            if failure:
                failed[c.candidate_id] = str(failure.get("reason", "failed"))
    stale: Counter[str] = Counter()
    other = foreign = 0
    all_keys = cache.keys()
    for key in all_keys:
        if key in expected:
            continue
        entry = cache.get(key) or {}
        version = (entry.get("provenance") or {}).get("prompt_version")
        candidate_id = (entry.get("assessment") or {}).get("candidate_id")
        if version != PROMPT_VERSION:
            stale[str(version)] += 1
        elif candidate_id in {c.candidate_id for cs in candidates.values() for c in cs}:
            other += 1  # same prompt, different inference profile or changed episode file
        else:
            foreign += 1  # belongs to episodes that are not part of this input
    source = "uploaded_bundle" if isinstance(cache, MemoryCache) else "local_cache"
    audit = _audit(cache.exists(), len(all_keys), len(ai), rejected, stale, other, foreign, source)
    return ai, failed, audit


def _audit(present, scanned, used, rejected, stale, other, foreign, source) -> CacheAudit:
    return CacheAudit(
        cache_dir_present=present,
        source=source,
        entries_scanned=scanned,
        used=used,
        invalid_on_revalidation=tuple(rejected),
        stale_prompt_version=sum(stale.values()),
        other_profile_or_input=other,
        not_in_this_input=foreign,
        stale_prompt_versions_seen=tuple(sorted(stale)),
    )


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def coverage(run: RunResult) -> EvaluationCoverage:
    total = len(run.by_id)
    covered = sorted({run.by_id[c].episode_number for c in run.ai})
    without = tuple(n for n in sorted(run.episodes) if n not in set(covered))
    return EvaluationCoverage(
        total_episodes=len(run.episodes),
        total_candidates=total,
        ai_evaluated=len(run.ai),
        not_evaluated=total - len(run.ai) - len(run.failed),
        failed=len(run.failed),
        episodes_with_ai=len(covered),
        episodes_without_ai=without,
        candidate_coverage=round(len(run.ai) / total, 4) if total else 0.0,
        episode_coverage=round(len(covered) / len(run.episodes), 4) if run.episodes else 0.0,
        complete=total > 0 and len(run.ai) == total,
        profile=run.profile,
        cache=run.audit,
    )


def run_summary(run: RunResult) -> RunSummary:
    issues = run.parse.issues
    excluded = sorted(
        {i.source for i in issues if i.severity is Severity.ERROR and i.source != run.parse.source}
    )
    return RunSummary(
        run_id=run.run_id,
        status="completed",
        created_at=run.created_at,
        input=run.input,
        episodes=len(run.episodes),
        scenes=sum(len(e.scenes) for e in run.episodes.values()),
        events=sum(len(e.events) for e in run.episodes.values()),
        candidates=len(run.by_id),
        parse=ParseSummary(
            errors=sum(i.severity is Severity.ERROR for i in issues),
            warnings=sum(i.severity is Severity.WARNING for i in issues),
            info=sum(i.severity is Severity.INFO for i in issues),
            excluded_sources=tuple(excluded),
            issue_codes=dict(sorted(Counter(i.code for i in issues).items())),
        ),
        coverage=coverage(run),
        timings_ms=run.timings_ms,
        versions={
            "promolens": __version__,
            "prompt": PROMPT_VERSION,
            "scoring_rules": SCORING_RULES_VERSION,
            "baseline": BASELINE_METHOD,
            "ranking": RANKING_RULES_VERSION,
        },
        live_ai_calls=run.live_ai_calls,
    )


# ---------------------------------------------------------------------------
# Input description
# ---------------------------------------------------------------------------


def describe_path(path: Path) -> InputInfo:
    if path.is_dir():
        digest = hashlib.sha256()
        files = sorted(p for p in path.rglob("*.txt") if p.is_file())
        size = 0
        for f in files:
            data = f.read_bytes()
            size += len(data)
            digest.update(f.relative_to(path).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(data).digest())
        return InputInfo(
            name=path.name,
            kind="directory",
            sha256=digest.hexdigest(),
            size_bytes=size,
            files=len(files),
        )
    data = path.read_bytes()
    kind = "zip" if path.suffix.lower() == ".zip" else "txt"
    return describe_bytes(path.name, data, kind)


def describe_bytes(name: str, data: bytes, kind: str, files: int = 1) -> InputInfo:
    return InputInfo(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        files=files,
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
