"""Privacy eligibility preflight: synthetic ZDR-list rows, mocked HTTP, no network."""

import json
from pathlib import Path

import httpx
import pytest

from framecue import eval_driver
from framecue.eligibility import Eligibility, assess, check_eligibility, zdr_list_url
from framecue.llm_client import LLMError, LLMSettings, OpenRouterClient
from llm_fakes import TEST_KEY, body, settings
from synthetic import FIXTURES

MODEL = "acme/test-model"
STRUCTURED = ["response_format", "structured_outputs", "max_tokens"]


def row(tag: str, provider: str, *, sr: bool | None = False, params=STRUCTURED, model=MODEL):
    r = {
        "model_id": model,
        "tag": tag,
        "provider_name": provider,
        "supported_parameters": list(params),
        "status": 0,
        "pricing": {"prompt": "0.0000002", "completion": "0.000001"},
    }
    if sr is not None:
        r["safety_retention"] = sr
    return r


def test_only_safety_retention_endpoints_block_by_default() -> None:
    rows = [row("bigcloud/global", "BigCloud", sr=True), row("bigcloud/eu", "BigCloud", sr=True)]
    result = assess(rows, settings(model=MODEL))
    assert not result.eligible and result.reason == "only_safety_retention_endpoints"
    assert {e.tag for e in result.excluded} == {"bigcloud/global", "bigcloud/eu"}
    assert not result.usable


def test_safety_retention_only_with_explicit_opt_in() -> None:
    rows = [row("bigcloud/global", "BigCloud", sr=True)]
    result = assess(rows, settings(model=MODEL, allow_safety_retention=True))
    assert result.eligible and result.reason == "eligible_with_safety_retention"


def test_strict_endpoints_win_and_retaining_providers_are_ignored() -> None:
    rows = [
        row("bigcloud/global", "BigCloud", sr=True),
        row("smallhost/fp8", "SmallHost"),
        row("otherhost", "OtherHost"),
    ]
    result = assess(rows, settings(model=MODEL))
    assert result.eligible and result.reason == "eligible_strict_zdr"
    assert {e.tag for e in result.usable} == {"smallhost/fp8", "otherhost"}
    assert result.ignore_providers == ("bigcloud",)
    assert result.forbidden_provider_names == frozenset({"BigCloud"})


def test_provider_with_both_kinds_cannot_be_separated() -> None:
    rows = [row("bigcloud/us", "BigCloud", sr=True), row("bigcloud/eu", "BigCloud")]
    result = assess(rows, settings(model=MODEL))
    assert not result.eligible and result.reason == "cannot_exclude_safety_retention"


def test_missing_model_unstructured_endpoints_and_unknown_flag() -> None:
    assert assess([row("x", "X", model="other/model")], settings(model=MODEL)).reason == (
        "no_zdr_endpoint"
    )
    unstructured = assess([row("x", "X", params=["max_tokens"])], settings(model=MODEL))
    assert unstructured.reason == "no_structured_output_zdr_endpoint"
    unknown = assess([row("x", "X", sr=None)], settings(model=MODEL))  # flag absent
    assert not unknown.eligible and unknown.reason == "only_safety_retention_endpoints"


def test_fetch_uses_public_list_without_key_and_fails_closed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [row("smallhost", "SmallHost")]})

    result = check_eligibility(settings(model=MODEL), httpx.MockTransport(handler))
    assert result.eligible and len(seen) == 1
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/endpoints/zdr"
    assert seen[0].method == "GET" and "authorization" not in seen[0].headers
    assert TEST_KEY not in str(seen[0].url)

    failing = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    assert check_eligibility(settings(model=MODEL), failing).reason == "eligibility_check_failed"
    wrong_shape = httpx.MockTransport(lambda r: httpx.Response(200, json=[1, 2]))
    assert check_eligibility(settings(model=MODEL), wrong_shape).reason == (
        "eligibility_check_failed"
    )
    off = check_eligibility(settings(model=MODEL, zdr=False), failing)
    assert off.reason == "zdr_disabled" and not off.eligible


def test_zdr_list_url_derivation() -> None:
    assert zdr_list_url("https://openrouter.ai/api/v1/chat/completions") == (
        "https://openrouter.ai/api/v1/endpoints/zdr"
    )
    assert zdr_list_url("https://proxy.test/v1/") == "https://proxy.test/v1/endpoints/zdr"


def test_allow_safety_retention_needs_an_explicit_yes(tmp_path) -> None:
    def parse(value: str) -> bool:
        env = {"FRAMECUE_LLM_ALLOW_SAFETY_RETENTION": value}
        return LLMSettings.from_env(env=env, dotenv=None).allow_safety_retention

    assert parse("true") and parse("1") and parse("YES")
    assert not parse("maybe") and not parse("false") and not parse("0")
    assert not LLMSettings.from_env(env={}, dotenv=None).allow_safety_retention


def _client(responses: list[httpx.Response], **kwargs):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    client = OpenRouterClient(settings(), httpx.MockTransport(handler), lambda _: None, **kwargs)
    return client, seen


MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "{}"}]


def test_client_sends_ignore_list_and_keeps_privacy_flags() -> None:
    client, seen = _client([httpx.Response(200, json=body("{}"))], ignore_providers=("bigcloud",))
    client.complete_json(MESSAGES, {"type": "object"}, "x")
    provider = json.loads(seen[0].content)["provider"]
    assert provider == {
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
        "ignore": ["bigcloud"],
    }


def test_response_from_forbidden_provider_aborts_without_retry() -> None:
    reply = body("{}")
    reply["provider"] = "BigCloud"
    client, seen = _client(
        [httpx.Response(200, json=reply), httpx.Response(200, json=reply)],
        forbidden_providers=frozenset({"BigCloud"}),
    )
    with pytest.raises(LLMError) as info:
        client.complete_json(MESSAGES, {"type": "object"}, "x")
    assert info.value.code == "privacy_routing_violation" and info.value.scope == "run"
    assert len(seen) == 1 and client.stats.failures == ["privacy_routing_violation"]


def test_driver_refuses_live_run_before_sending_anything(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("FRAMECUE_LLM_API_KEY", TEST_KEY)
    blocked = Eligibility(MODEL, False, "only_safety_retention_endpoints", "synthetic")
    monkeypatch.setattr(eval_driver, "check_eligibility", lambda s: blocked)

    def no_client(*args, **kwargs):
        raise AssertionError("the model client must not be created")

    monkeypatch.setattr(eval_driver, "ChatCompletionsClient", no_client)
    fixture = Path(FIXTURES) / "synthetic_episode_01.txt"
    code = eval_driver.main(["--zip", str(fixture), "--per-episode", "2", "--live"])
    out = capsys.readouterr().out
    assert code == 3 and "refusing: only_safety_retention_endpoints" in out
    assert TEST_KEY not in out and not (tmp_path / "runs").exists()
