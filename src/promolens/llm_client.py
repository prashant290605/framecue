"""A small OpenAI-compatible chat-completions client for OpenRouter or GroqCloud.

``PROMOLENS_LLM_PROVIDER`` selects the request dialect (default ``openrouter``).

**GroqCloud** (``groq``): ``max_completion_tokens``, strict ``json_schema`` (constrained
decoding on supported models), optional ``reasoning_effort`` and ``include_reasoning=false``.
No OpenRouter ``provider`` routing object is sent; data retention is governed by the Groq
organisation's Data Controls (ZDR), which cannot be verified through the API. The client never
sends ``service_tier``, so requests use the default on-demand tier. It also enforces free-plan
safety:

- responses reporting a non on-demand ``service_tier`` abort the run;
- rate-limit headers above the known free-plan ceiling for the model abort the run
  (``paid_plan_suspected``), because higher limits mean the organisation's plan changed;
- requests are paced from the ``x-ratelimit-*`` headers to stay under tokens-per-minute;
- daily-quota 429s stop the run (``daily_quota_exhausted``) instead of hammering the API.

**OpenRouter** (``openrouter``): privacy routing is enforced in every request. The
``provider`` object asks for:

- ``require_parameters=true``: only endpoints that honour the JSON-schema response format.
- ``data_collection="deny"``: no endpoints that store prompts.
- ``zdr=true``: zero-data-retention endpoints only.

``zdr=true`` alone still admits "Safety Retention" endpoints on accounts that allow them, so
callers run ``promolens.eligibility.check_eligibility`` first. Its result supplies
``ignore_providers`` (sent as ``provider.ignore``) and ``forbidden_providers`` (a response from
one of them aborts the run with ``privacy_routing_violation``).

If no endpoint qualifies, OpenRouter returns an error. That error is reported as the
run-level ``no_eligible_provider`` and never retried with weaker settings. No tools, plugins
or web search are requested.

The API key is held as a ``SecretStr``. It is never logged, included in errors, or repr'd.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

Provider = Literal["openrouter", "groq"]
PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "openrouter": (DEFAULT_ENDPOINT, DEFAULT_MODEL),
    "groq": (GROQ_ENDPOINT, GROQ_DEFAULT_MODEL),
}
PROVIDER_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}

# Free-plan (requests/day, tokens/minute) ceilings per Groq model, as reported by the
# x-ratelimit-limit-* headers on 2026-11-10. Higher values mean the organisation's plan changed.
GROQ_FREE_PLAN_LIMITS: dict[str, tuple[int, int]] = {"openai/gpt-oss-120b": (1_000, 8_000)}
MAX_RETRY_WAIT_S = 60.0
# Measured on live Groq runs (2026-11-10): ~3.1 chars/token for prompt v2. Used only for pacing.
CHARS_PER_TOKEN_ESTIMATE = 3.0

_ENV = {
    "provider": ("PROMOLENS_LLM_PROVIDER",),
    "api_key": ("PROMOLENS_LLM_API_KEY",),
    "model": ("PROMOLENS_LLM_MODEL",),
    "endpoint": ("PROMOLENS_LLM_ENDPOINT",),
    "timeout_s": ("PROMOLENS_LLM_TIMEOUT_S",),
    "max_output_tokens": ("PROMOLENS_LLM_MAX_OUTPUT_TOKENS",),
    "max_retries": ("PROMOLENS_LLM_MAX_RETRIES",),
    "reasoning_effort": ("PROMOLENS_LLM_REASONING_EFFORT",),
    "zdr": ("PROMOLENS_LLM_ZDR",),
    "allow_safety_retention": ("PROMOLENS_LLM_ALLOW_SAFETY_RETENTION",),
    "batch_size": ("PROMOLENS_EVAL_BATCH_SIZE",),
    "max_run_cost": ("PROMOLENS_MAX_RUN_COST",),
    "price_prompt_per_mtok": ("PROMOLENS_PRICE_PROMPT_PER_MTOK",),
    "price_completion_per_mtok": ("PROMOLENS_PRICE_COMPLETION_PER_MTOK",),
    "daily_token_budget": ("PROMOLENS_DAILY_TOKEN_BUDGET",),
}

# Groq free plan allows 8,000 tokens/minute for gpt-oss-120b, so a single request (prompt plus
# output) must stay well under that. Measured on the real pack: see docs/PLAN.md §10b.
GROQ_TUNED_DEFAULTS: dict[str, object] = {
    "batch_size": 4,
    "max_output_tokens": 2_500,
    "reasoning_effort": "low",  # chosen after a live low-vs-medium comparison (PLAN §10b)
}


class LLMSettings(BaseModel):
    """Inference settings. Load them with ``from_env``, which reads the environment and then
    an optional ``.env`` file. Environment variables win."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider = "openrouter"
    api_key: SecretStr | None = None
    model: str = DEFAULT_MODEL  # replaced by the provider default when not given
    endpoint: str = DEFAULT_ENDPOINT  # replaced by the provider default when not given
    timeout_s: float = Field(default=60.0, gt=0)
    max_output_tokens: int = Field(default=4_000, gt=0)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_retries: int = Field(default=3, ge=0, le=10)
    # Groq reasoning models only. None leaves the provider default (medium on gpt-oss).
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    zdr: bool = True
    # Opt-in only: accept ZDR-listed endpoints that keep flagged prompts for abuse review.
    allow_safety_retention: bool = False
    data_collection: Literal["deny"] = "deny"  # deliberately not configurable to "allow"
    batch_size: int = Field(default=8, ge=1, le=32)
    max_run_cost: float | None = Field(default=None, gt=0, description="Stop when exceeded.")
    price_prompt_per_mtok: float | None = Field(default=None, ge=0)
    price_completion_per_mtok: float | None = Field(default=None, ge=0)
    # Free-tier guard: tokens this project may spend per UTC day (see promolens.quota).
    daily_token_budget: int | None = Field(default=None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _provider_defaults(cls, data: object) -> object:
        if isinstance(data, dict):
            provider = data.get("provider") or "openrouter"
            if provider in PROVIDER_DEFAULTS:
                endpoint, model = PROVIDER_DEFAULTS[provider]
                extra = GROQ_TUNED_DEFAULTS if provider == "groq" else {}
                data = {"endpoint": endpoint, "model": model, **extra, **data}
        return data

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, dotenv: Path | None = Path(".env")
    ) -> "LLMSettings":
        values = {**_read_dotenv(dotenv), **(os.environ if env is None else env)}
        found: dict[str, str] = {}
        for name, keys in _ENV.items():
            for key in keys:
                if values.get(key, "").strip():
                    found[name] = values[key].strip()
                    break
        provider = found.get("provider", "openrouter").lower()
        found["provider"] = provider
        if "api_key" not in found:  # provider-specific fallback, never another provider's key
            fallback = values.get(PROVIDER_KEY_ENV.get(provider, ""), "").strip()
            if fallback:
                found["api_key"] = fallback
        if "zdr" in found:
            found["zdr"] = str(found["zdr"].lower() not in {"0", "false", "no"})
        if "allow_safety_retention" in found:  # relaxing privacy needs an explicit yes
            flag = found["allow_safety_retention"].lower() in {"1", "true", "yes"}
            found["allow_safety_retention"] = str(flag)
        return cls.model_validate(found)

    def inference_identity(self) -> dict[str, object]:
        """Everything that can change model output, used in cache keys. It never includes
        the key."""
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "zdr": self.zdr,
            "allow_safety_retention": self.allow_safety_retention,
            "data_collection": self.data_collection,
        }

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """An ESTIMATE from configured prices, or None when prices are not configured."""
        if self.price_prompt_per_mtok is None or self.price_completion_per_mtok is None:
            return None
        return (
            prompt_tokens * self.price_prompt_per_mtok
            + completion_tokens * self.price_completion_per_mtok
        ) / 1_000_000


def _read_dotenv(path: Path | None) -> dict[str, str]:
    """A minimal ``KEY=VALUE`` reader. Comments and blank lines are ignored, and nothing
    is exported."""
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class LLMError(Exception):
    """A classified failure. ``scope="run"`` means every further request would fail too (bad
    key, no credits, no privacy-compatible endpoint, unsupported model), so the caller stops."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        scope: Literal["run", "batch"] = "batch",
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.scope = scope
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None  # as reported by the provider; never estimated here


@dataclass(frozen=True)
class LLMResponse:
    content: str
    finish_reason: str | None
    model: str | None
    provider: str | None
    usage: TokenUsage
    latency_ms: int
    service_tier: str | None = None


@dataclass(frozen=True)
class RateLimitSnapshot:
    """``x-ratelimit-*`` headers. On Groq, *requests* are per day and *tokens* per minute."""

    limit_requests: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    observed_at: float = 0.0  # time.monotonic()


@dataclass
class ClientStats:
    http_attempts: int = 0
    http_retries: int = 0
    failures: list[str] = field(default_factory=list)
    # code, HTTP status and truncated provider message; for private diagnosis files only
    failure_details: list[str] = field(default_factory=list)
    pacing_sleeps: int = 0
    pacing_sleep_s: float = 0.0
    rate_limits: RateLimitSnapshot | None = None


class ChatCompletionsClient:
    """``complete_json`` makes one logical request, with bounded retries for transient errors.

    Response *content* problems (bad JSON, schema violations, truncation) are not retried
    here. The evaluator handles them by repairing or splitting the batch.
    """

    def __init__(
        self,
        settings: LLMSettings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        *,
        ignore_providers: tuple[str, ...] = (),
        forbidden_providers: frozenset[str] = frozenset(),
    ) -> None:
        if settings.api_key is None or not settings.api_key.get_secret_value().strip():
            raise LLMError(
                "missing_api_key",
                "Set PROMOLENS_LLM_API_KEY in the environment or .env",
                scope="run",
            )
        self.settings = settings
        self.ignore_providers = tuple(ignore_providers)
        self.forbidden_providers = frozenset(forbidden_providers)
        self.stats = ClientStats()
        self._sleep = sleep
        self._clock = time.monotonic
        self._http = httpx.Client(timeout=settings.timeout_s, transport=transport)

    def close(self) -> None:
        self._http.close()

    def build_payload(self, messages: list[dict[str, str]], schema: dict, name: str) -> dict:
        s = self.settings
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
        if s.provider == "groq":
            payload: dict[str, object] = {
                "model": s.model,
                "messages": messages,
                "temperature": s.temperature,
                "max_completion_tokens": s.max_output_tokens,
                "stream": False,
                "response_format": response_format,
                "include_reasoning": False,
            }
            if s.reasoning_effort:
                payload["reasoning_effort"] = s.reasoning_effort
            return payload
        provider: dict[str, object] = {
            "require_parameters": True,
            "data_collection": s.data_collection,
            "zdr": s.zdr,
        }
        if self.ignore_providers:
            provider["ignore"] = list(self.ignore_providers)
        return {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": s.max_output_tokens,
            "stream": False,
            "response_format": response_format,
            "provider": provider,
        }

    def complete_json(self, messages: list[dict[str, str]], schema: dict, name: str) -> LLMResponse:
        payload = self.build_payload(messages, schema, name)
        key = self.settings.api_key.get_secret_value()  # type: ignore[union-attr]
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        need = self._estimate_tokens(payload)
        for attempt in range(self.settings.max_retries + 1):
            try:
                self._pace(need)
            except LLMError as exc:  # rejected locally before sending: record it too
                self.stats.failures.append(f"{exc.code}_local")
                raise
            self.stats.http_attempts += 1
            started = time.perf_counter()
            try:
                response = self._http.post(self.settings.endpoint, json=payload, headers=headers)
                self._observe(response)
                result = _parse(response, int((time.perf_counter() - started) * 1000))
                self._check_plan(result)
            except httpx.TimeoutException:
                error = LLMError("timeout", "request timed out", retryable=True)
            except httpx.TransportError as exc:
                error = LLMError("network_error", type(exc).__name__, retryable=True)
            except LLMError as exc:
                error = exc
            else:
                if result.provider in self.forbidden_providers:
                    self.stats.failures.append("privacy_routing_violation")
                    raise LLMError(
                        "privacy_routing_violation",
                        "the response came from a provider excluded by the eligibility check",
                        scope="run",
                    )
                if result.provider is None and self.settings.provider == "groq":
                    result = replace(result, provider="groq")
                return result
            self.stats.failures.append(error.code)
            self.stats.failure_details.append(
                f"{error.code} http={error.status} {error.message[:160]}"
            )
            if error.retryable and (error.retry_after or 0) > MAX_RETRY_WAIT_S:
                raise LLMError(
                    "rate_limit_wait_too_long",
                    f"provider asked to wait {error.retry_after:.0f}s; progress is cached",
                    scope="run",
                    status=error.status,
                    retry_after=error.retry_after,
                )
            if not error.retryable or attempt == self.settings.max_retries:
                raise error
            self.stats.http_retries += 1
            self._sleep(_backoff(attempt, error.retry_after))
        raise AssertionError("unreachable")  # pragma: no cover

    # -- Groq free-plan safety and pacing -------------------------------------------

    def _estimate_tokens(self, payload: dict) -> int:
        chars = sum(len(m["content"]) for m in payload["messages"])
        chars += len(json.dumps(payload["response_format"]))
        # Reserve the full output allowance: TPM 429s occurred when only half was reserved.
        return int(chars / CHARS_PER_TOKEN_ESTIMATE) + self.settings.max_output_tokens

    def _observe(self, response: httpx.Response) -> None:
        h = response.headers
        if "x-ratelimit-limit-tokens" not in h and "x-ratelimit-limit-requests" not in h:
            return
        self.stats.rate_limits = RateLimitSnapshot(
            limit_requests=_int(h.get("x-ratelimit-limit-requests")),
            remaining_requests=_int(h.get("x-ratelimit-remaining-requests")),
            limit_tokens=_int(h.get("x-ratelimit-limit-tokens")),
            remaining_tokens=_int(h.get("x-ratelimit-remaining-tokens")),
            reset_tokens_s=parse_duration(h.get("x-ratelimit-reset-tokens")),
            observed_at=self._clock(),
        )

    def _pace(self, need: int) -> None:
        """Wait until the tokens-per-minute budget can absorb this request. Tokens refill
        continuously at limit/60 per second (consistent with the reset headers observed)."""
        snap = self.stats.rate_limits
        if self.settings.provider != "groq" or snap is None:
            return
        if snap.remaining_requests == 0:
            raise LLMError(
                "daily_quota_exhausted", "requests-per-day quota is used up", scope="run"
            )
        if snap.limit_tokens is None or snap.remaining_tokens is None:
            return
        if need > snap.limit_tokens:
            raise LLMError(
                "request_too_large",
                f"estimated {need} tokens exceeds the {snap.limit_tokens} tokens/minute limit",
            )
        rate = snap.limit_tokens / 60.0
        elapsed = max(0.0, self._clock() - snap.observed_at)
        available = min(snap.limit_tokens, snap.remaining_tokens + elapsed * rate)
        if need > available:
            wait = min(MAX_RETRY_WAIT_S, (need - available) / rate + 0.5)
            self.stats.pacing_sleeps += 1
            self.stats.pacing_sleep_s += wait
            self._sleep(wait)

    def _check_plan(self, result: LLMResponse) -> None:
        if self.settings.provider != "groq":
            return
        if result.service_tier not in (None, "on_demand"):
            raise LLMError(
                "paid_tier_used",
                f"response reports service_tier={result.service_tier!r}; only on_demand allowed",
                scope="run",
            )
        ceiling = GROQ_FREE_PLAN_LIMITS.get(self.settings.model)
        snap = self.stats.rate_limits
        if ceiling and snap:
            rpd, tpm = ceiling
            if (snap.limit_requests or 0) > rpd or (snap.limit_tokens or 0) > tpm:
                raise LLMError(
                    "paid_plan_suspected",
                    "rate limits exceed the known free-plan ceiling; check the Groq plan",
                    scope="run",
                )


OpenRouterClient = ChatCompletionsClient  # backwards-compatible name


def parse_duration(value: str | None) -> float | None:
    """Parse Groq reset durations such as ``8.475s``, ``1m26.4s``, ``2h3m`` or ``120ms``."""
    if not value:
        return None
    total, number = 0.0, ""
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    i = 0
    try:
        while i < len(value):
            ch = value[i]
            if ch.isdigit() or ch == ".":
                number += ch
                i += 1
                continue
            unit = "ms" if value.startswith("ms", i) else ch
            total += float(number) * units[unit]
            number = ""
            i += len(unit)
        if number:
            total += float(number)
    except (KeyError, ValueError):
        return None
    return total


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(max(retry_after, 0.0), 60.0)
    return min(2.0**attempt, 30.0)


def _parse(response: httpx.Response, latency_ms: int) -> LLMResponse:
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        if response.status_code != 200:
            raise _http_error(response.status_code, "", response) from None
        raise LLMError("malformed_http_body", "response body is not JSON", retryable=True) from None
    if not isinstance(body, dict):
        raise LLMError("malformed_http_body", "response body is not an object", retryable=True)
    error = body.get("error")
    if response.status_code != 200 or error:
        status = response.status_code
        if isinstance(error, dict) and isinstance(error.get("code"), int) and status == 200:
            status = error["code"]  # in-body error on HTTP 200
        message = str(error.get("message", "")) if isinstance(error, dict) else ""
        raise _http_error(status, message, response)
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise LLMError("empty_response", "no choices returned", retryable=True)
    choice = choices[0]
    finish = choice.get("finish_reason")
    content = (choice.get("message") or {}).get("content")
    if finish == "length":
        raise LLMError("truncated", "output hit max_tokens before completing")
    if finish == "content_filter":
        raise LLMError("content_filtered", "the provider refused or filtered the output")
    if finish == "error":
        raise LLMError("provider_error", "the provider reported an error", retryable=True)
    if not isinstance(content, str) or not content.strip():
        raise LLMError("empty_content", "the message content is empty")
    usage = body.get("usage") or {}
    cost = usage.get("cost")
    return LLMResponse(
        content=content,
        finish_reason=finish,
        model=body.get("model"),
        provider=body.get("provider"),
        usage=TokenUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cost=float(cost) if isinstance(cost, int | float) else None,
        ),
        latency_ms=latency_ms,
        service_tier=tier if isinstance(tier := body.get("service_tier"), str) else None,
    )


def _http_error(status: int, message: str, response: httpx.Response) -> LLMError:
    """Map an HTTP or in-body error to a classified LLMError. Messages are truncated provider
    text, which never contains our key."""
    short = message[:200]
    lower = message.lower()
    retry_after = _retry_after(response)
    if status == 401:
        return LLMError("auth_failed", "invalid or disabled API key", scope="run", status=status)
    if status == 402:
        return LLMError("insufficient_credits", short or "add credits", scope="run", status=status)
    if status == 404 or ("no endpoints" in lower) or ("routing requirements" in lower):
        return LLMError(
            "no_eligible_provider",
            "model not found, or no endpoint meets the privacy/parameter requirements "
            "(constraints are never relaxed automatically)",
            scope="run",
            status=status,
        )
    if status == 413:
        return LLMError("request_too_large", short or "request too large", status=status)
    if status == 400:
        if "json_validate_failed" in lower or "failed to generate json" in lower:
            return LLMError("schema_generation_failed", short, status=status)
        if "blocked_api_access" in lower or "spend limit" in lower:
            return LLMError("spend_limit_reached", short, scope="run", status=status)
        if "context" in lower and ("length" in lower or "window" in lower or "token" in lower):
            return LLMError("context_too_long", short, status=status)
        if "model" in lower and ("not a valid" in lower or "invalid model" in lower):
            return LLMError("unsupported_model", short, scope="run", status=status)
        unsupported = ("response_format", "json_schema", "structured", "not support")
        if any(word in lower for word in unsupported):
            return LLMError("unsupported_parameters", short, scope="run", status=status)
        return LLMError("bad_request", short, status=status)
    if status == 403:
        return LLMError("forbidden", short or "request blocked", status=status)
    if status in (408, 504):
        return LLMError("timeout", short or "upstream timeout", retryable=True, status=status)
    if status == 429:
        if "per day" in lower or "(tpd)" in lower or "(rpd)" in lower:
            return LLMError(
                "daily_quota_exhausted",
                "daily request or token quota is used up; progress is cached",
                scope="run",
                status=status,
                retry_after=retry_after,
            )
        return LLMError(
            "rate_limited", "rate limited", retryable=True, status=status, retry_after=retry_after
        )
    if status >= 500:
        return LLMError(
            "server_error", short, retryable=True, status=status, retry_after=retry_after
        )
    return LLMError(f"http_{status}", short, status=status)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
