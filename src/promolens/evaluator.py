"""Semantic promo evaluation: batched LLM calls, strict evidence validation, recovery, cache.

Contract:
- The model returns 0-5 judgements, evidence event ids and short explanations. Python checks
  every id against the parsed episode and computes the weighted score.
- Every candidate ends with exactly one ``PromoEvaluation``. Its status is LLM_EVALUATED,
  FAILED, or HEURISTIC_FALLBACK (clearly labelled, with no AI scores).
- A batch that comes back partial, invalid or truncated is never accepted as a whole.
  Individually valid items are kept. The rest are retried in smaller batches, with
  validation feedback, until ``max_attempts`` is reached.
- A run-level error stops all further requests, and every remaining candidate is marked.
  Run-level errors are: bad key, no credits, no privacy-compatible endpoint, unsupported
  model, budget exhausted. One episode failing never crashes the run.
"""

import hashlib
import json
import os
import re
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from promolens.candidates import EpisodeIndex, intersects
from promolens.llm_client import LLMError, LLMResponse, LLMSettings
from promolens.prompts import (
    PROMPT_VERSION,
    SCHEMA_NAME,
    alias_maps,
    build_messages,
    response_schema,
    to_real_ids,
)
from promolens.schemas import (
    CandidateConfig,
    Episode,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStats,
    EvaluationStatus,
    ModelCandidateAssessment,
    PromoCandidate,
    PromoEvaluation,
    RubricWeights,
    weighted_score,
)

SCORING_RULES_VERSION = "evidence-rules-v1"  # bump when local validation rules change
_EVENT_ID_IN_TEXT = re.compile(r"\bE\d{3,}-S\d{2,}-\d{4,}\b")


class JsonModelClient(Protocol):
    def complete_json(
        self, messages: list[dict[str, str]], schema: dict, name: str
    ) -> LLMResponse: ...


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_size: int = Field(default=8, ge=1, le=32)
    max_attempts: int = Field(default=3, ge=1, le=6, description="Model requests per candidate.")
    rubric: RubricWeights = RubricWeights()
    heuristic_fallback: bool = True
    force_refresh: bool = False
    max_run_cost: float | None = Field(default=None, gt=0)
    # Free-tier quota guard: stop sending once this run has used this many tokens.
    max_run_tokens: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class EvaluationCache:
    """A private on-disk cache of validated model assessments, one JSON file per key.

    Only the validated assessment and its provenance are stored, never prompts or raw
    responses. The directory must be gitignored (``runs/`` is).
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None  # a corrupt entry counts as a miss and will be overwritten

    def put(self, key: str, value: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        self.clear_failure(key)

    def keys(self) -> list[str]:
        """Every cached assessment key, sorted (for audits)."""
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("[0-9a-f][0-9a-f]/*.json"))

    # Failure records: candidates a live run genuinely tried and could not evaluate. They
    # hold only ids, a reason code and attempt counts. A later valid assessment replaces them.

    def _failure_path(self, key: str) -> Path:
        return self.root / "failures" / f"{key}.json"

    def record_failure(self, key: str, candidate_id: str, reason: str, attempts: int) -> None:
        path = self._failure_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"candidate_id": candidate_id, "reason": reason, "attempts": attempts}
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    def get_failure(self, key: str) -> dict | None:
        path = self._failure_path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (json.JSONDecodeError, OSError):
            return None

    def clear_failure(self, key: str) -> None:
        self._failure_path(key).unlink(missing_ok=True)

    def exists(self) -> bool:
        return self.root.is_dir()

    def export_bundle(self) -> dict:
        """Every cached assessment and failure record as one JSON-able bundle, for importing
        private AI results into a stateless deployment at request time. It contains model
        explanations about confidential scripts: keep it private."""
        entries = {k: e for k in self.keys() if (e := self.get(k)) is not None}
        failures = {}
        folder = self.root / "failures"
        if folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                if (record := self.get_failure(path.stem)) is not None:
                    failures[path.stem] = record
        return {"format": BUNDLE_FORMAT, "entries": entries, "failures": failures}


BUNDLE_FORMAT = "promolens-ai-bundle-v1"
MAX_BUNDLE_ENTRIES = 5_000
_KEY = re.compile(r"^[0-9a-f]{64}$")


class BundleError(ValueError):
    pass


class MemoryCache:
    """A read-only stand-in for ``EvaluationCache`` built from an uploaded AI bundle. Entries
    still go through ``load_cached_evaluation``, so they are used only when their key matches
    the uploaded screenplay exactly and their evidence re-validates."""

    def __init__(self, bundle: object) -> None:
        if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
            raise BundleError(f"not a {BUNDLE_FORMAT} file")
        entries, failures = bundle.get("entries"), bundle.get("failures", {})
        if not isinstance(entries, dict) or not isinstance(failures, dict):
            raise BundleError("bundle entries must be objects keyed by cache key")
        if len(entries) + len(failures) > MAX_BUNDLE_ENTRIES:
            raise BundleError(f"bundle has more than {MAX_BUNDLE_ENTRIES} records")
        bad = [k for k in [*entries, *failures] if not isinstance(k, str) or not _KEY.match(k)]
        if bad:
            raise BundleError("bundle keys must be 64-character hex cache keys")
        self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
        self._failures = {k: v for k, v in failures.items() if isinstance(v, dict)}

    def get(self, key: str) -> dict | None:
        return self._entries.get(key)

    def get_failure(self, key: str) -> dict | None:
        return self._failures.get(key)

    def keys(self) -> list[str]:
        return sorted(self._entries)

    def exists(self) -> bool:
        return True


def load_cached_evaluation(
    cache: EvaluationCache,
    key: str,
    episode: Episode,
    candidate: PromoCandidate,
    candidate_config: CandidateConfig,
    rubric: RubricWeights,
) -> tuple[PromoEvaluation | None, bool]:
    """Return (evaluation, rejected). A cached entry is used only if it parses and its
    evidence re-validates against the parsed episode, so a stale or tampered entry cannot
    slip through. ``rejected`` is True when an entry existed but failed those checks."""
    entry = cache.get(key)
    if entry is None:
        return None, False
    try:
        assessment = ModelCandidateAssessment.model_validate(entry["assessment"])
        provenance = EvaluationProvenance.model_validate(entry["provenance"])
    except (KeyError, TypeError, ValidationError):
        return None, True
    check = validate_batch(
        episode,
        [candidate],
        {"evaluations": [assessment.model_dump(mode="json")]},
        candidate_config,
    )
    if candidate.candidate_id not in check.valid:
        return None, True
    provenance = provenance.model_copy(update={"from_cache": True})
    return _evaluation(candidate, assessment, provenance, rubric), False


def cache_key(
    episode: Episode,
    candidate: PromoCandidate,
    settings: LLMSettings,
    candidate_config: CandidateConfig,
) -> str:
    """Everything that can change the model's assessment of this candidate.

    Rubric *weights* are left out on purpose. The weighted score is recomputed from the cached
    0-5 judgements on every read, so changing weights never serves a stale score and never
    forces a paid re-evaluation.
    """
    material = {
        "episode_sha256": episode.source_sha256,
        "candidate": [candidate.candidate_id, candidate.start_ms, candidate.end_ms],
        "prompt_version": PROMPT_VERSION,
        "scoring_rules": SCORING_RULES_VERSION,
        "inference": settings.inference_identity(),
        "windows": [
            candidate_config.hook_window_ms,
            candidate_config.ending_window_ms,
            candidate_config.context_window_ms,
        ],
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation of model output
# ---------------------------------------------------------------------------


@dataclass
class BatchCheck:
    valid: dict[str, ModelCandidateAssessment] = field(default_factory=dict)
    problems: dict[str, list[str]] = field(default_factory=dict)
    batch_problems: list[str] = field(default_factory=list)


def parse_content(content: str) -> object:
    """Parse JSON content. The only leniency is a single surrounding ``` fence, which some
    endpoints add even under json_schema."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def validate_batch(
    episode: Episode,
    candidates: list[PromoCandidate],
    data: object,
    config: CandidateConfig,
) -> BatchCheck:
    """Validate a parsed response against the batch and the source episode."""
    check = BatchCheck()
    expected = {c.candidate_id: c for c in candidates}
    items = data.get("evaluations") if isinstance(data, dict) else None
    if not isinstance(items, list):
        check.batch_problems.append("response has no 'evaluations' array")
        items = []
    counts = Counter(i.get("candidate_id") for i in items if isinstance(i, dict))
    index = EpisodeIndex(episode)
    by_id = {e.event_id: e for e in index.timeline}
    for item in items:
        cid = item.get("candidate_id") if isinstance(item, dict) else None
        if cid not in expected:
            check.batch_problems.append(f"unknown candidate_id {cid!r:.40}")
            continue
        if counts[cid] > 1:
            check.problems.setdefault(cid, []).append(f"{cid}: returned {counts[cid]} times")
            continue
        try:
            assessment = ModelCandidateAssessment.model_validate(item)
        except ValidationError as exc:
            fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
            check.problems.setdefault(cid, []).append(f"{cid}: schema violation in {fields}")
            continue
        issues = _evidence_issues(assessment, expected[cid], by_id, episode, config)
        if issues:
            check.problems.setdefault(cid, []).extend(issues)
        else:
            check.valid[cid] = assessment
    for cid in expected:
        if cid not in check.valid and cid not in check.problems:
            check.problems[cid] = [f"{cid}: missing from response"]
    return check


def _evidence_issues(
    a: ModelCandidateAssessment,
    c: PromoCandidate,
    by_id: dict,
    episode: Episode,
    config: CandidateConfig,
) -> list[str]:
    """Check the evidence ids by real interval intersection, never by string or timestamp
    matching."""
    issues: list[str] = []
    prefix = f"E{episode.episode_number:03d}-"
    hook_window = (c.start_ms, min(c.end_ms, c.start_ms + config.hook_window_ms))
    tail_window = (max(c.start_ms, c.end_ms - config.ending_window_ms), c.end_ms)
    checks = (
        ("hook_event_ids", a.hook_event_ids, hook_window, "the first 3 s"),
        ("cliffhanger_event_ids", a.cliffhanger_event_ids, tail_window, "the final 5 s"),
    )
    for name, ids, (lo, hi), where in checks:
        if len(set(ids)) != len(ids):
            issues.append(f"{c.candidate_id}: {name} repeats an id")
        for event_id in ids:
            issue = _id_issue(event_id, prefix, by_id, lo, hi)
            if issue:
                issues.append(f"{c.candidate_id}: {name} {event_id} {issue} {where}")
    if not a.hook_event_ids and a.hook_score > 1:
        issues.append(f"{c.candidate_id}: hook_score {a.hook_score} needs hook evidence (max 1)")
    if not a.cliffhanger_event_ids and a.cliffhanger_score > 1:
        issues.append(
            f"{c.candidate_id}: cliffhanger_score {a.cliffhanger_score} needs evidence (max 1)"
        )
    texts = (a.hook_explanation, a.cliffhanger_explanation, a.why_it_works, a.weakness)
    for event_id in sorted({m for t in texts for m in _EVENT_ID_IN_TEXT.findall(t)}):
        issue = _id_issue(event_id, prefix, by_id, c.start_ms, c.end_ms)
        if issue:
            issues.append(f"{c.candidate_id}: explanation cites {event_id}, {issue} the clip")
    return issues


def _id_issue(event_id: str, prefix: str, by_id: dict, lo: int, hi: int) -> str | None:
    if not event_id.startswith(prefix):
        return "belongs to another episode, not"
    event = by_id.get(event_id)
    if event is None:
        return "does not exist, so is not in"
    if not intersects(event, lo, hi):
        return "does not intersect"
    return None


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


@dataclass
class _Stats:
    requests: int = 0
    successful: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reported_cost: float = 0.0
    cost_responses: int = 0
    latency_ms: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    models: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)


class Evaluator:
    def __init__(
        self,
        client: JsonModelClient | None,
        settings: LLMSettings,
        config: EvaluationConfig | None = None,
        candidate_config: CandidateConfig | None = None,
        cache: EvaluationCache | None = None,
        raw_log: Path | None = None,
    ) -> None:
        self.client = client
        # Optional JSONL of UNMODIFIED model responses (private; under runs/). Validated
        # results are derived from these but never written back into them.
        self.raw_log = raw_log
        self._keys: dict[str, str] = {}
        self.settings = settings
        self.config = config or EvaluationConfig()
        self.candidate_config = candidate_config or CandidateConfig()
        self.cache = cache
        self.stats = _Stats()
        self.aborted_reason: str | None = None if client else "no_llm_client"
        # Local validation problems per failed attempt (ids and codes only, no script text).
        self.validation_log: list[str] = []

    # -- public ------------------------------------------------------------

    def evaluate_run(self, items: Iterable[tuple[Episode, list[PromoCandidate]]]) -> EvaluationRun:
        evaluations: list[PromoEvaluation] = []
        for episode, candidates in items:
            evaluations.extend(self.evaluate_episode(episode, candidates))
        counts = Counter(e.status for e in evaluations)
        s = self.stats
        estimate = self.settings.estimate_cost(s.prompt_tokens, s.completion_tokens)
        stats = EvaluationRunStats(
            requests=s.requests,
            successful_responses=s.successful,
            failed_requests=s.failed,
            http_retries=getattr(getattr(self.client, "stats", None), "http_retries", 0),
            prompt_tokens=s.prompt_tokens,
            completion_tokens=s.completion_tokens,
            reported_cost=round(s.reported_cost, 6) if s.cost_responses else None,
            responses_with_reported_cost=s.cost_responses,
            estimated_cost_usd=round(estimate, 6) if estimate is not None else None,
            latency_ms_total=s.latency_ms,
            cache_hits=s.cache_hits,
            cache_misses=s.cache_misses,
            evaluated=counts[EvaluationStatus.LLM_EVALUATED],
            failed=counts[EvaluationStatus.FAILED],
            fallback=counts[EvaluationStatus.HEURISTIC_FALLBACK],
            models_reported=tuple(sorted(s.models)),
            providers_reported=tuple(sorted(s.providers)),
        )
        return EvaluationRun(
            evaluations=tuple(evaluations),
            stats=stats,
            prompt_version=PROMPT_VERSION,
            model_requested=self.settings.model,
            aborted_reason=self.aborted_reason,
        )

    def evaluate_episode(
        self, episode: Episode, candidates: list[PromoCandidate]
    ) -> list[PromoEvaluation]:
        """Return one evaluation per candidate, in input order. Never raises on model errors."""
        results: dict[str, PromoEvaluation] = {}
        keys = {
            c.candidate_id: cache_key(episode, c, self.settings, self.candidate_config)
            for c in candidates
        }
        self._keys = keys
        todo = [c for c in candidates if not self._from_cache(episode, c, keys, results)]
        todo.sort(key=lambda c: (c.start_ms, c.end_ms))  # deterministic, context-friendly
        size = self.config.batch_size
        queue = deque(todo[i : i + size] for i in range(0, len(todo), size))
        attempts: Counter[str] = Counter()
        notes: dict[str, list[str]] = {}
        while queue:
            batch = queue.popleft()
            reason = self._stop_reason()
            if reason:
                self._fail(batch, reason, attempts, results)
                continue
            for c in batch:
                attempts[c.candidate_id] += 1
            outcome = self._request(episode, batch, notes)
            if isinstance(outcome, LLMError):
                if outcome.scope == "run":
                    self.aborted_reason = outcome.code
                    self._fail(batch, outcome.code, attempts, results)
                    continue
                failed, why = batch, outcome.code
                if outcome.code == "request_too_large" and len(batch) > 1:
                    # A size split is not a model attempt; it ends at single candidates.
                    for c in batch:
                        attempts[c.candidate_id] -= 1
            else:
                response, check = outcome
                for cid, assessment in check.valid.items():
                    results[cid] = self._accept(
                        episode, cid, batch, assessment, response, attempts[cid], keys[cid]
                    )
                failed = [c for c in batch if c.candidate_id not in check.valid]
                for c in failed:
                    notes[c.candidate_id] = check.problems.get(c.candidate_id, [])[:6]
                why = "validation_failed"
                if check.batch_problems:
                    for c in failed:
                        notes[c.candidate_id] = notes[c.candidate_id] + check.batch_problems[:3]
                for c in failed:  # ids and codes only, never script text
                    self.validation_log.append(
                        f"attempt {attempts[c.candidate_id]}: "
                        + ("; ".join(notes[c.candidate_id]) or f"{c.candidate_id}: missing")
                    )
            self._retry_or_fail(failed, why, attempts, queue, results)
        return [results[c.candidate_id] for c in candidates]

    # -- internals -----------------------------------------------------------

    def _request(
        self, episode: Episode, batch: list[PromoCandidate], notes: dict[str, list[str]]
    ) -> tuple[LLMResponse, BatchCheck] | LLMError:
        repair = [n for c in batch for n in notes.get(c.candidate_id, [])]
        messages = build_messages(episode, batch, self.candidate_config, repair or None)
        schema = response_schema([c.candidate_id for c in batch])
        self.stats.requests += 1
        try:
            response = self.client.complete_json(messages, schema, SCHEMA_NAME)  # type: ignore[union-attr]
        except LLMError as exc:
            self.stats.failed += 1
            return exc
        self._record_usage(response)
        self._log_raw(episode, batch, response)
        try:
            data = parse_content(response.content)
        except (json.JSONDecodeError, IndexError):
            self.stats.failed += 1
            return LLMError("malformed_json", "content is not valid JSON")
        self.stats.successful += 1
        data = to_real_ids(data, alias_maps(episode)[0])  # aliases -> real ids, then validate
        return response, validate_batch(episode, batch, data, self.candidate_config)

    def _log_raw(
        self, episode: Episode, batch: list[PromoCandidate], response: LLMResponse
    ) -> None:
        if self.raw_log is None:
            return
        record = {
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "episode_number": episode.episode_number,
            "episode_sha256": episode.source_sha256,
            "candidate_ids": [c.candidate_id for c in batch],
            "prompt_version": PROMPT_VERSION,
            "model_requested": self.settings.model,
            "model_reported": response.model,
            "provider": response.provider,
            "finish_reason": response.finish_reason,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "latency_ms": response.latency_ms,
            "content": response.content,  # exactly as returned, before alias mapping
        }
        self.raw_log.parent.mkdir(parents=True, exist_ok=True)
        with self.raw_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _record_usage(self, response: LLMResponse) -> None:
        s = self.stats
        s.prompt_tokens += response.usage.prompt_tokens
        s.completion_tokens += response.usage.completion_tokens
        s.latency_ms += response.latency_ms
        if response.usage.cost is not None:
            s.reported_cost += response.usage.cost
            s.cost_responses += 1
        if response.model:
            s.models.add(response.model)
        if response.provider:
            s.providers.add(response.provider)

    def _stop_reason(self) -> str | None:
        if self.aborted_reason:
            return self.aborted_reason
        token_limit = self.config.max_run_tokens
        used = self.stats.prompt_tokens + self.stats.completion_tokens
        if token_limit is not None and used >= token_limit:
            self.aborted_reason = "token_budget_exhausted"
            return self.aborted_reason
        limit = self.config.max_run_cost
        if limit is None or self.stats.requests == 0:
            return None
        spent: float | None = self.stats.reported_cost if self.stats.cost_responses else None
        if spent is None:
            spent = self.settings.estimate_cost(
                self.stats.prompt_tokens, self.stats.completion_tokens
            )
        if spent is None:
            self.aborted_reason = "budget_unenforceable_no_cost_data"
        elif spent >= limit:
            self.aborted_reason = "budget_exhausted"
        return self.aborted_reason

    def _retry_or_fail(
        self,
        failed: list[PromoCandidate],
        reason: str,
        attempts: Counter[str],
        queue: deque,
        results: dict[str, PromoEvaluation],
    ) -> None:
        """Requeue at the front, split in half when more than one candidate is left, or fail
        candidates whose attempts are used up."""
        retry = [c for c in failed if attempts[c.candidate_id] < self.config.max_attempts]
        exhausted = [c for c in failed if c not in retry]
        self._fail(
            exhausted, f"{reason}_after_{self.config.max_attempts}_attempts", attempts, results
        )
        if len(retry) > 1:
            half = (len(retry) + 1) // 2
            queue.extendleft([retry[half:], retry[:half]])
        elif retry:
            queue.appendleft(retry)

    def _fail(
        self,
        batch: list[PromoCandidate],
        reason: str,
        attempts: Counter[str],
        results: dict[str, PromoEvaluation],
    ) -> None:
        fallback = self.config.heuristic_fallback
        genuine = reason.endswith("_attempts")  # tried and failed, not merely stopped early
        for c in batch:
            key = self._keys.get(c.candidate_id)
            if genuine and self.cache is not None and key:
                self.cache.record_failure(key, c.candidate_id, reason, attempts[c.candidate_id])
            results[c.candidate_id] = PromoEvaluation(
                candidate_id=c.candidate_id,
                episode_number=c.episode_number,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                status=(
                    EvaluationStatus.HEURISTIC_FALLBACK if fallback else EvaluationStatus.FAILED
                ),
                heuristic_priority=c.heuristic.priority,
                failure_reason=reason,
                provenance=EvaluationProvenance(
                    evaluator="heuristic_fallback" if fallback else "none",
                    model_requested=self.settings.model,
                    prompt_version=PROMPT_VERSION,
                    attempts=attempts[c.candidate_id],
                ),
            )

    def _accept(
        self,
        episode: Episode,
        cid: str,
        batch: list[PromoCandidate],
        assessment: ModelCandidateAssessment,
        response: LLMResponse,
        attempts: int,
        key: str,
    ) -> PromoEvaluation:
        candidate = next(c for c in batch if c.candidate_id == cid)
        provenance = EvaluationProvenance(
            evaluator="llm",
            model_requested=self.settings.model,
            model_reported=response.model,
            provider=response.provider,
            prompt_version=PROMPT_VERSION,
            attempts=attempts,
            cache_key=key,
        )
        if self.cache is not None:
            self.cache.put(
                key,
                {
                    "assessment": assessment.model_dump(mode="json"),
                    "provenance": provenance.model_dump(mode="json"),
                },
            )
        return _evaluation(candidate, assessment, provenance, self.config.rubric)

    def _from_cache(
        self,
        episode: Episode,
        candidate: PromoCandidate,
        keys: dict[str, str],
        results: dict[str, PromoEvaluation],
    ) -> bool:
        if self.cache is None or self.config.force_refresh:
            if self.cache is not None:
                self.stats.cache_misses += 1
            return False
        evaluation, _ = load_cached_evaluation(
            self.cache,
            keys[candidate.candidate_id],
            episode,
            candidate,
            self.candidate_config,
            self.config.rubric,
        )
        if evaluation is not None:
            self.stats.cache_hits += 1
            results[candidate.candidate_id] = evaluation
            return True
        self.stats.cache_misses += 1
        return False


def _evaluation(
    candidate: PromoCandidate,
    a: ModelCandidateAssessment,
    provenance: EvaluationProvenance,
    rubric: RubricWeights,
) -> PromoEvaluation:
    scores = a.scores()
    return PromoEvaluation(
        candidate_id=candidate.candidate_id,
        episode_number=candidate.episode_number,
        start_ms=candidate.start_ms,
        end_ms=candidate.end_ms,
        status=EvaluationStatus.LLM_EVALUATED,
        scores=scores,
        weighted_score=weighted_score(scores, rubric),
        rubric_weights=rubric,
        hook_event_ids=a.hook_event_ids,
        cliffhanger_event_ids=a.cliffhanger_event_ids,
        hook_explanation=a.hook_explanation,
        cliffhanger_explanation=a.cliffhanger_explanation,
        why_it_works=a.why_it_works,
        weakness=a.weakness,
        warnings=a.warnings,
        heuristic_priority=candidate.heuristic.priority,
        provenance=provenance,
    )


def estimate_prompt_tokens(
    episode: Episode,
    candidates: list[PromoCandidate],
    batch_size: int,
    config: CandidateConfig,
    chars_per_token: float = 4.0,
) -> int:
    """A rough ESTIMATE of prompt tokens (characters / 4) for planning and cost gating only."""
    ordered = sorted(candidates, key=lambda c: (c.start_ms, c.end_ms))
    total = 0
    for i in range(0, len(ordered), batch_size):
        messages = build_messages(episode, ordered[i : i + batch_size], config)
        total += sum(len(m["content"]) for m in messages)
    return int(total / chars_per_token)
