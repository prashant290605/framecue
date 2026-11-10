"""GroqCloud dialect, free-plan guards, pacing and quota handling. Fully mocked: no network."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from framecue.candidates import generate_candidates
from framecue.eligibility import check_eligibility
from framecue.evaluator import EvaluationCache, EvaluationConfig, Evaluator
from framecue.llm_client import (
    GROQ_ENDPOINT,
    ChatCompletionsClient,
    LLMError,
    LLMSettings,
    parse_duration,
)
from framecue.quota import DailyLedger
from framecue.schemas import CandidateConfig, EvaluationStatus
from llm_fakes import TEST_KEY, FakeOpenRouter, body, request_ids
from test_evaluator import make_episode

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "{}"}]
SCHEMA = {"type": "object"}
FREE = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-limit-tokens": "8000"}


def groq(**overrides) -> LLMSettings:
    return LLMSettings(provider="groq", api_key=TEST_KEY, max_retries=2, **overrides)


def headers(remaining_tokens: int = 7000, remaining_requests: int = 990, **extra) -> dict:
    return {
        **FREE,
        "x-ratelimit-remaining-requests": str(remaining_requests),
        "x-ratelimit-remaining-tokens": str(remaining_tokens),
        "x-ratelimit-reset-tokens": "7.5s",
        **extra,
    }


def groq_body(content="{}", tier="on_demand") -> dict:
    b = body(content, cost=None)
    b.pop("provider")
    b["model"] = "openai/gpt-oss-120b"
    b["service_tier"] = tier
    return b


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def client_for(responses: list, **overrides):
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    clock = Clock()

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.t += seconds

    client = ChatCompletionsClient(groq(**overrides), httpx.MockTransport(handler), sleep)
    client._clock = clock
    return client, seen, sleeps, clock


def ok(**kw) -> httpx.Response:
    return httpx.Response(200, json=groq_body(), headers=headers(**kw))


def call(client):
    return client.complete_json(MESSAGES, SCHEMA, "x")


def test_settings_defaults_and_provider_specific_key_fallback() -> None:
    s = LLMSettings.from_env(
        env={"FRAMECUE_LLM_PROVIDER": "groq", "GROQ_API_KEY": "g"}, dotenv=None
    )
    assert s.provider == "groq" and s.endpoint == GROQ_ENDPOINT
    assert s.model == "openai/gpt-oss-120b" and s.batch_size == 4 and s.max_output_tokens == 2500
    assert s.api_key.get_secret_value() == "g" and s.reasoning_effort == "low"
    other = {"FRAMECUE_LLM_PROVIDER": "groq", "OPENROUTER_API_KEY": "not-for-groq"}
    assert LLMSettings.from_env(env=other, dotenv=None).api_key is None
    explicit = LLMSettings.from_env(
        env={"FRAMECUE_LLM_PROVIDER": "groq", "FRAMECUE_EVAL_BATCH_SIZE": "2"}, dotenv=None
    )
    assert explicit.batch_size == 2
    assert LLMSettings().provider == "openrouter" and LLMSettings().batch_size == 8


def test_groq_payload_has_no_openrouter_routing_or_billing_fields() -> None:
    client, seen, _, _ = client_for([ok()], reasoning_effort="low")
    response = call(client)
    payload = json.loads(seen[0].content)
    assert "provider" not in payload and "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 2500 and payload["include_reasoning"] is False
    assert payload["reasoning_effort"] == "low" and payload["stream"] is False
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    forbidden = {"service_tier", "tools", "tool_choice", "plugins", "web_search_options"}
    assert not forbidden & set(payload)
    assert str(seen[0].url) == GROQ_ENDPOINT
    assert response.provider == "groq" and response.service_tier == "on_demand"
    assert response.usage.cost is None  # Groq reports no cost; none is invented


def test_daily_quota_429_stops_the_run_without_retrying() -> None:
    msg = "Rate limit reached for model on tokens per day (TPD): Limit 200000, Used 199000"
    resp = httpx.Response(
        429,
        json={"error": {"message": msg, "code": "rate_limit_exceeded"}},
        headers={"retry-after": "700"},
    )
    client, seen, sleeps, _ = client_for([resp])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "daily_quota_exhausted" and info.value.scope == "run"
    assert len(seen) == 1 and sleeps == []


def test_tpm_429_honours_retry_after_and_long_waits_stop() -> None:
    tpm = httpx.Response(
        429, json={"error": {"message": "tokens per minute (TPM)"}}, headers={"retry-after": "3"}
    )
    client, seen, sleeps, _ = client_for([tpm, ok()])
    call(client)
    assert len(seen) == 2 and sleeps[0] == 3.0

    long = httpx.Response(429, json={"error": {"message": "slow"}}, headers={"retry-after": "900"})
    client, seen, sleeps, _ = client_for([long])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "rate_limit_wait_too_long" and info.value.scope == "run"
    assert len(seen) == 1


def test_size_and_schema_errors_are_batch_level_and_spend_limit_is_run_level() -> None:
    cases = [
        (413, "Request too large for model", "request_too_large", "batch"),
        (400, "json_validate_failed: Failed to generate JSON", "schema_generation_failed", "batch"),
        (400, "blocked_api_access: spend limit reached", "spend_limit_reached", "run"),
    ]
    for status, message, code, scope in cases:
        client, seen, _, _ = client_for(
            [httpx.Response(status, json={"error": {"message": message}})]
        )
        with pytest.raises(LLMError) as info:
            call(client)
        assert (info.value.code, info.value.scope, len(seen)) == (code, scope, 1)
        detail = client.stats.failure_details[-1]
        assert detail.startswith(f"{code} http={status}") and TEST_KEY not in detail


def test_paid_service_tier_or_raised_limits_abort_the_run() -> None:
    flex = httpx.Response(200, json=groq_body(tier="flex"), headers=headers())
    client, _, _, _ = client_for([flex])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "paid_tier_used" and info.value.scope == "run"

    raised = httpx.Response(
        200, json=groq_body(), headers={**headers(), "x-ratelimit-limit-tokens": "250000"}
    )
    client, _, _, _ = client_for([raised])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "paid_plan_suspected" and info.value.scope == "run"


def test_pacing_waits_for_the_token_bucket_to_refill() -> None:
    client, seen, sleeps, clock = client_for([ok(remaining_tokens=500), ok()])
    call(client)
    call(client)  # needs ~2,500+ tokens (full output allowance + prompt) but only 500 are left
    need = client._estimate_tokens(client.build_payload(MESSAGES, SCHEMA, "x"))
    assert len(seen) == 2 and len(sleeps) == 1
    assert sleeps[0] == pytest.approx((need - 500) / (8000 / 60) + 0.5)
    assert client.stats.pacing_sleeps == 1


def test_no_sleep_when_tokens_refilled_and_requests_exhausted_stops() -> None:
    client, _, sleeps, clock = client_for([ok(remaining_tokens=500), ok()])
    call(client)
    clock.t += 60  # a full minute later the bucket is full again
    call(client)
    assert sleeps == []

    client, seen, _, _ = client_for([ok(remaining_requests=0)])
    call(client)
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "daily_quota_exhausted" and len(seen) == 1


def test_parse_duration_formats() -> None:
    assert parse_duration("8.475s") == pytest.approx(8.475)
    assert parse_duration("1m26.4s") == pytest.approx(86.4)
    assert parse_duration("2h3m") == pytest.approx(7380)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration(None) is None and parse_duration("bad") is None


def test_ledger_counts_per_utc_day(tmp_path) -> None:
    day = [datetime(2026, 11, 10, 23, 0, tzinfo=UTC)]
    ledger = DailyLedger.for_model(tmp_path, "groq", "openai/gpt-oss-120b", now=lambda: day[0])
    assert ledger.tokens_today() == 0
    ledger.add(2, 1000, 300)
    ledger.add(1, 500, 100)
    assert ledger.used_today() == {"requests": 3, "prompt_tokens": 1500, "completion_tokens": 400}
    day[0] = datetime(2026, 11, 11, 0, 5, tzinfo=UTC)
    assert ledger.tokens_today() == 0
    assert ledger.path.name == "groq-openai_gpt-oss-120b.json"


def test_token_budget_stops_requests_and_keeps_accepted_work(tmp_path) -> None:
    episode = make_episode()
    cands = list(generate_candidates(episode).shortlist)[:8]
    fake = FakeOpenRouter(cands)
    client, _ = fake.client()
    cache = EvaluationCache(tmp_path)
    config = EvaluationConfig(batch_size=4, max_run_tokens=1000)
    run = Evaluator(client, groq(), config, CandidateConfig(), cache).evaluate_run(
        [(episode, cands)]
    )
    assert len(fake.payloads) == 1 and run.aborted_reason == "token_budget_exhausted"
    assert run.stats.evaluated == 4 and run.stats.fallback == 4
    fallback = [e for e in run.evaluations if e.status == EvaluationStatus.HEURISTIC_FALLBACK]
    assert all(e.failure_reason == "token_budget_exhausted" and e.scores is None for e in fallback)
    # resume: the 4 accepted are cached; only the rest are requested
    fake2 = FakeOpenRouter(cands)
    client2, _ = fake2.client()
    resumed = Evaluator(client2, groq(), EvaluationConfig(batch_size=4), CandidateConfig(), cache)
    result = resumed.evaluate_run([(episode, cands)])
    assert result.stats.cache_hits == 4 and len(fake2.payloads) == 1
    assert sorted(request_ids(fake2.payloads[0])) == sorted(e.candidate_id for e in fallback)
    assert result.stats.evaluated == 8


def test_groq_eligibility_relies_on_account_zdr_without_network() -> None:
    def no_network(request):
        raise AssertionError("no request expected")

    result = check_eligibility(groq(), httpx.MockTransport(no_network))
    assert result.eligible and result.reason == "groq_account_zdr"


def test_size_split_does_not_consume_model_attempts() -> None:
    episode = make_episode()
    cands = list(generate_candidates(episode).shortlist)[:4]
    too_large = lambda p, ids: httpx.Response(413, json={"error": {"message": "too large"}})  # noqa: E731
    fake = FakeOpenRouter(cands, [too_large])
    client, _ = fake.client()
    config = EvaluationConfig(batch_size=4, max_attempts=1)
    run = Evaluator(client, groq(), config, CandidateConfig()).evaluate_run([(episode, cands)])
    assert [len(request_ids(p)) for p in fake.payloads] == [4, 2, 2]
    assert run.stats.evaluated == 4 and run.stats.fallback == 0
    assert all(e.provenance.attempts == 1 for e in run.evaluations)
