"""HTTP-level behaviour of the OpenRouter client, fully mocked (no network)."""

import httpx
import pydantic
import pytest

from llm_fakes import TEST_KEY, body, error, settings
from promolens.llm_client import LLMError, LLMSettings, OpenRouterClient

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "{}"}]
SCHEMA = {"type": "object"}


def client_for(responses: list, **overrides):
    """Responses are httpx.Response objects or exceptions, consumed in order."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = OpenRouterClient(settings(**overrides), httpx.MockTransport(handler), sleeps.append)
    return client, seen, sleeps


def call(client: OpenRouterClient):
    return client.complete_json(MESSAGES, SCHEMA, "x")


def test_payload_enforces_privacy_routing_and_structured_output() -> None:
    client, seen, _ = client_for([httpx.Response(200, json=body("{}"))])
    response = call(client)
    payload = __import__("json").loads(seen[0].content)
    assert payload["provider"] == {
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert payload["temperature"] == 0.0 and payload["stream"] is False
    assert not {"tools", "plugins", "tool_choice", "web_search_options"} & set(payload)
    assert seen[0].headers["authorization"] == f"Bearer {TEST_KEY}"
    assert response.provider == "Google AI Studio" and response.usage.cost == 0.0001
    assert response.usage.prompt_tokens == 1000 and response.usage.completion_tokens == 200


def test_missing_api_key_is_a_clear_run_level_error() -> None:
    with pytest.raises(LLMError) as info:
        OpenRouterClient(LLMSettings())
    assert info.value.code == "missing_api_key" and info.value.scope == "run"


def test_settings_from_env_and_dotenv_never_expose_key(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# local\nPROMOLENS_LLM_API_KEY=from-dotenv\nPROMOLENS_EVAL_BATCH_SIZE=6\n",
        encoding="utf-8",
    )
    s = LLMSettings.from_env(env={"PROMOLENS_LLM_ZDR": "true"}, dotenv=dotenv)
    assert s.api_key.get_secret_value() == "from-dotenv" and s.batch_size == 6 and s.zdr
    assert "from-dotenv" not in repr(s) and "from-dotenv" not in str(s.inference_identity())
    env_wins = LLMSettings.from_env(env={"OPENROUTER_API_KEY": "from-env"}, dotenv=dotenv)
    assert env_wins.api_key.get_secret_value() == "from-dotenv"  # PROMOLENS_ name has priority
    with pytest.raises(pydantic.ValidationError):
        LLMSettings(data_collection="allow")  # privacy can't be relaxed by configuration


def test_rate_limit_backs_off_using_retry_after() -> None:
    client, seen, sleeps = client_for(
        [error(429, "slow down", {"Retry-After": "7"}), httpx.Response(200, json=body("{}"))]
    )
    call(client)
    assert len(seen) == 2 and sleeps == [7.0] and client.stats.http_retries == 1


def test_server_errors_retry_with_bounded_backoff_then_fail() -> None:
    client, seen, sleeps = client_for([error(502), error(500), error(503, "overloaded")])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "server_error" and info.value.retryable
    assert len(seen) == 3 and sleeps == [1.0, 2.0]  # max_retries=2


def test_timeouts_are_retried() -> None:
    client, seen, _ = client_for([httpx.ReadTimeout("slow"), httpx.Response(200, json=body("{}"))])
    assert call(client).content == "{}" and len(seen) == 2


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (error(401, "No auth credentials found"), "auth_failed"),
        (error(402, "Insufficient credits"), "insufficient_credits"),
        (error(400, "response_format json_schema is not supported"), "unsupported_parameters"),
        (error(400, "foo/bar is not a valid model ID"), "unsupported_model"),
        (error(404, "No endpoints found matching your data policy"), "no_eligible_provider"),
        (
            error(
                503, "There is no available model provider that meets your routing requirements."
            ),
            "no_eligible_provider",
        ),
    ],
)
def test_run_level_errors_are_not_retried(response, code: str) -> None:
    client, seen, _ = client_for([response])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == code and info.value.scope == "run"
    assert len(seen) == 1
    assert TEST_KEY not in str(info.value)


def test_error_inside_a_200_body_is_detected() -> None:
    client, _, _ = client_for([httpx.Response(200, json={"error": {"code": 402, "message": "x"}})])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "insufficient_credits"


@pytest.mark.parametrize(
    ("finish", "code"),
    [("length", "truncated"), ("content_filter", "content_filtered")],
)
def test_bad_finish_reasons_are_errors(finish: str, code: str) -> None:
    client, seen, _ = client_for([httpx.Response(200, json=body('{"evaluations": [', finish))])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == code and info.value.scope == "batch" and len(seen) == 1


def test_non_json_body_and_empty_content() -> None:
    client, _, _ = client_for(
        [httpx.Response(200, text="<html>"), httpx.Response(200, text="<html>"),
         httpx.Response(200, text="<html>")]
    )  # fmt: skip
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "malformed_http_body"
    empty = body("")
    client, _, _ = client_for([httpx.Response(200, json=empty)])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.code == "empty_content"


def test_cost_absent_is_not_invented() -> None:
    client, _, _ = client_for([httpx.Response(200, json=body("{}", cost=None))])
    assert call(client).usage.cost is None
