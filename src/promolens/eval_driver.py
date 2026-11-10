"""Minimal PRIVATE driver for verifying the evaluator on the real pack. This is not the final
CLI.

Without ``--live`` it only plans. It reports how many candidates and requests there would be,
how many are already cached (coverage), an ESTIMATE of tokens, and for OpenRouter an estimated
cost from configured prices. It makes no network calls unless ``--check-eligibility`` is
given. For OpenRouter that reads the public ZDR endpoint list; no key or screenplay content is
sent.

``--live`` always runs the eligibility check first (see ``promolens.eligibility``).

Provider-specific guards:

- **OpenRouter:** refuses models with no strict-ZDR, structured-output endpoint. Large runs
  need ``PROMOLENS_MAX_RUN_COST``.
- **GroqCloud (free plan):** cost is never estimated as spend. A daily token budget
  (``PROMOLENS_DAILY_TOKEN_BUDGET``, default 180,000) is enforced with a local ledger under
  ``runs/quota/``. Requests are paced to the tokens-per-minute headers, and a daily-quota 429
  stops the run. Accepted assessments are cached, so re-running the same command resumes where
  it stopped.

With ``--live`` it writes full results, including the explanations, to ``runs/eval-smoke/``
(gitignored). The console shows ids, scores, warnings and usage only, never screenplay text.

    python -m promolens.eval_driver --zip <pack.zip> --episodes 1 --per-episode 4
    python -m promolens.eval_driver --zip <pack.zip> --episodes 1 --per-episode 4 --live
    python -m promolens.eval_driver --zip <pack.zip> --candidates C001-... C007-... --live
    python -m promolens.eval_driver --zip <pack.zip> --per-episode 0 --live --allow-large
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from promolens.candidates import generate_candidates
from promolens.eligibility import check_eligibility
from promolens.evaluator import (
    EvaluationCache,
    EvaluationConfig,
    Evaluator,
    cache_key,
    estimate_prompt_tokens,
)
from promolens.llm_client import ChatCompletionsClient, LLMError, LLMSettings
from promolens.parser import parse_path
from promolens.quota import DailyLedger
from promolens.schemas import CandidateConfig

LIVE_CANDIDATE_LIMIT = 40  # larger live runs need --allow-large plus a cost or token cap
GROQ_DEFAULT_DAILY_TOKEN_BUDGET = 180_000  # below the documented 200K TPD, with headroom


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = LLMSettings.from_env()
    groq = settings.provider == "groq"
    candidate_config = CandidateConfig()
    episodes = parse_path(args.zip).raise_for_errors().episodes
    wanted = set(args.episodes) if args.episodes else None
    chosen = set(args.candidates or [])
    plan = []
    for episode in episodes:
        if wanted is not None and episode.episode_number not in wanted:
            continue
        shortlist = list(generate_candidates(episode, candidate_config).shortlist)
        if chosen:
            shortlist = [c for c in shortlist if c.candidate_id in chosen]
        elif args.per_episode and args.schedule == "shortlist":
            shortlist = shortlist[: args.per_episode]
        if shortlist:
            plan.append((episode, shortlist))
    if chosen and args.schedule != "shortlist":
        print("refusing: --candidates cannot be combined with --schedule coverage-first")
        return 2
    missing = chosen - {c.candidate_id for _, cs in plan for c in cs}
    if missing:
        print(f"refusing: unknown candidate ids: {sorted(missing)}")
        return 2
    cache = EvaluationCache(Path(args.cache_dir))
    total = sum(len(c) for _, c in plan)
    cached = sum(
        1
        for e, cs in plan
        for c in cs
        if cache.get(cache_key(e, c, settings, candidate_config)) is not None
    )
    batch = args.batch_size or settings.batch_size
    todo_plan = plan if args.force_refresh else _uncached(plan, cache, settings, candidate_config)
    if args.schedule == "coverage-first":

        def is_cached(episode, candidate) -> bool:
            if args.force_refresh:
                return False
            return cache.get(cache_key(episode, candidate, settings, candidate_config)) is not None

        plan = todo_plan = coverage_first(plan, is_cached, args.levels)
        print(f"schedule=coverage-first levels={args.levels}: {len(plan)} candidates selected")
    todo = sum(len(c) for _, c in todo_plan)
    tokens = sum(estimate_prompt_tokens(e, c, batch, candidate_config) for e, c in todo_plan)
    out_tokens = todo * 220  # ESTIMATE: about 220 output tokens per assessment
    requests = sum(-(-len(c) // batch) for _, c in todo_plan)
    print(f"provider={settings.provider} model={settings.model}")
    print(
        f"reasoning_effort={settings.reasoning_effort} max_output_tokens="
        f"{settings.max_output_tokens}"
    )
    print(
        f"episodes={len(plan)} candidates={total} cached={cached} coverage={cached / total:.1%}"
        if total
        else "no candidates selected"
    )
    print(f"to_evaluate={todo} batch_size={batch} requests~{requests} (before retries)")
    print(f"ESTIMATED prompt_tokens~{tokens} completion_tokens~{out_tokens} (excl. reasoning)")
    ledger = DailyLedger.for_model(Path(args.quota_dir), settings.provider, settings.model)
    daily_budget = settings.daily_token_budget or (
        GROQ_DEFAULT_DAILY_TOKEN_BUDGET if groq else None
    )
    if groq:
        print(
            "cost: Groq Free Plan, no charge expected (Groq reports no cost; nothing billed "
            "is estimated)"
        )
        print(f"daily token budget={daily_budget} used_today(ledger)={ledger.tokens_today()}")
    else:
        estimate = settings.estimate_cost(tokens, out_tokens)
        note = "(set PROMOLENS_PRICE_* to estimate)" if estimate is None else f"~{estimate:.4f}"
        print(f"ESTIMATED cost: {note}  cap={settings.max_run_cost}")
    if not args.live:
        if args.check_eligibility:
            print(check_eligibility(settings).summary())
        print("dry run: no model API calls made (pass --live to evaluate)")
        return 0

    if todo > LIVE_CANDIDATE_LIMIT and not args.allow_large:
        print(f"refusing: {todo} candidates > {LIVE_CANDIDATE_LIMIT}; pass --allow-large")
        return 2
    max_run_tokens = None
    max_run_cost = None
    if groq:
        max_run_tokens = max(0, daily_budget - ledger.tokens_today())
        if max_run_tokens == 0:
            print("refusing: today's token budget is used up; re-run tomorrow (progress cached)")
            return 4
    else:
        max_run_cost = settings.max_run_cost
        if args.allow_large and max_run_cost is None:
            print("refusing: --allow-large requires PROMOLENS_MAX_RUN_COST")
            return 2
        estimate = settings.estimate_cost(tokens, out_tokens)
        if estimate is not None and max_run_cost and estimate > max_run_cost:
            print("refusing: estimated cost exceeds PROMOLENS_MAX_RUN_COST")
            return 2
    if settings.api_key is None:
        print("cannot start: missing_api_key: set PROMOLENS_LLM_API_KEY in the environment or .env")
        return 2
    eligibility = check_eligibility(settings)
    print(eligibility.summary())
    if not eligibility.eligible:
        print(f"refusing: {eligibility.reason}: {eligibility.detail}")
        return 3
    try:
        client = ChatCompletionsClient(
            settings,
            ignore_providers=eligibility.ignore_providers,
            forbidden_providers=eligibility.forbidden_provider_names,
        )
    except LLMError as exc:
        print(f"cannot start: {exc.code}: {exc.message}")
        return 2
    evaluator = Evaluator(
        client,
        settings,
        EvaluationConfig(
            batch_size=batch,
            force_refresh=args.force_refresh,
            max_run_cost=max_run_cost,
            max_run_tokens=max_run_tokens,
        ),
        candidate_config,
        cache,
        raw_log=Path(args.raw_dir) / _safe(f"{settings.provider}-{settings.model}.jsonl"),
    )
    try:
        run = evaluator.evaluate_run(plan)
    finally:
        client.close()
        s = evaluator.stats
        ledger.add(s.requests, s.prompt_tokens, s.completion_tokens)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluations.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    client_stats = {**asdict(client.stats), "validation_log": evaluator.validation_log}
    (out / "client_stats.json").write_text(json.dumps(client_stats, indent=1), encoding="utf-8")
    for e in run.evaluations:
        scores = e.scores.model_dump() if e.scores else None
        warnings = [w.value for w in e.warnings]
        print(f"{e.candidate_id} {e.status.value} score={e.weighted_score} {scores} {warnings}")
    print(json.dumps(run.stats.model_dump(), indent=1))
    console = {
        k: v for k, v in client_stats.items() if k not in ("failure_details", "validation_log")
    }
    console["validation_failures"] = len(evaluator.validation_log)
    print(f"client: {json.dumps(console)}")  # provider messages stay in client_stats.json
    done = sum(
        1
        for e, cs in plan
        for c in cs
        if cache.get(cache_key(e, c, settings, candidate_config)) is not None
    )
    print(f"coverage after run: {done}/{total} cached ({done / total:.1%})")
    print(f"ledger today: {ledger.used_today()}")
    print(f"aborted_reason={run.aborted_reason} results={out / 'evaluations.json'}")
    return 0


def coverage_first(plan, is_cached, levels: int):
    """Deterministic, coverage-first selection.

    Level 1 picks the best not-yet-evaluated candidate (in shortlist order) from every
    episode that has no AI assessment yet. Level 2 does the same for episodes with fewer than
    two, and so on. Episodes are visited in number order. Returns one (episode, [candidate])
    item per pick, so a daily budget stop leaves the widest possible episode coverage.
    """
    counts = {e.episode_number: sum(is_cached(e, c) for c in cs) for e, cs in plan}
    queues = {e.episode_number: [c for c in cs if not is_cached(e, c)] for e, cs in plan}
    picks = []
    for level in range(1, levels + 1):
        for episode, _ in plan:
            n = episode.episode_number
            if counts[n] < level and queues[n]:
                picks.append((episode, [queues[n].pop(0)]))
                counts[n] += 1
    return picks


def _safe(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _uncached(plan, cache, settings, candidate_config):
    out = []
    for episode, cs in plan:
        rest = [
            c for c in cs if cache.get(cache_key(episode, c, settings, candidate_config)) is None
        ]
        if rest:
            out.append((episode, rest))
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--zip", required=True)
    p.add_argument("--episodes", type=int, nargs="*")
    p.add_argument("--per-episode", type=int, default=4, help="0 = whole shortlist")
    p.add_argument("--candidates", nargs="*", help="evaluate exactly these candidate ids")
    p.add_argument("--batch-size", type=int)
    p.add_argument("--live", action="store_true", help="actually call the model API")
    p.add_argument("--allow-large", action="store_true")
    p.add_argument(
        "--check-eligibility",
        action="store_true",
        help="dry run: also read the public ZDR endpoint list (no key or content sent)",
    )
    p.add_argument("--force-refresh", action="store_true", help="ignore cached assessments")
    p.add_argument("--cache-dir", default="runs/cache/evaluations")
    p.add_argument("--quota-dir", default="runs/quota")
    p.add_argument("--raw-dir", default="runs/raw", help="unmodified model responses (private)")
    p.add_argument(
        "--schedule",
        choices=["shortlist", "coverage-first"],
        default="shortlist",
        help="coverage-first: one strong candidate per uncovered episode before alternatives",
    )
    p.add_argument("--levels", type=int, default=1, help="coverage-first: target per episode")
    p.add_argument("--out", default="runs/eval-smoke")
    return p


if __name__ == "__main__":
    sys.exit(main())
