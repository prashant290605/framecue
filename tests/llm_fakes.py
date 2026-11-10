"""Offline fakes for the OpenRouter API: httpx.MockTransport handlers that never touch the
network."""

import json
from collections.abc import Callable

import httpx

from promolens.llm_client import LLMSettings, OpenRouterClient
from promolens.schemas import PromoCandidate

TEST_KEY = "sk-test-NOT-A-REAL-KEY-0000"
REPORTED_MODEL = "google/gemini-3.1-flash-lite-20260507"
REPORTED_PROVIDER = "Google AI Studio"


def settings(**overrides) -> LLMSettings:
    return LLMSettings(api_key=TEST_KEY, max_retries=2, **overrides)


def body(content: str | dict, finish: str = "stop", cost: float | None = 0.0001) -> dict:
    text = content if isinstance(content, str) else json.dumps(content)
    usage = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": "gen-test",
        "model": REPORTED_MODEL,
        "provider": REPORTED_PROVIDER,
        "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}],
        "usage": usage,
    }


def ok(items: list[dict], **kwargs) -> httpx.Response:
    return httpx.Response(200, json=body({"evaluations": items}, **kwargs))


def error(status: int, message: str = "error", headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": message}}, headers=headers
    )


def good_item(c: PromoCandidate, **overrides) -> dict:
    item = {
        "candidate_id": c.candidate_id,
        "hook_score": 3,
        "cliffhanger_score": 3,
        "standalone_score": 3,
        "pacing_score": 3,
        "hook_event_ids": [c.opening_event_ids[0]],
        "cliffhanger_event_ids": [c.ending_event_ids[-1]],
        "hook_explanation": "A sharp line lands immediately.",
        "cliffhanger_explanation": "The question is left open at the cut.",
        "why_it_works": "Clear conflict between two people with an open ending.",
        "weakness": "Stakes are only implied.",
        "warnings": [],
    }
    return {**item, **overrides}


Step = Callable[[dict, list[str]], httpx.Response]


class FakeOpenRouter:
    """Records every request. Plays scripted steps first, then answers every candidate in the
    request (read from the schema's candidate_id enum) with a valid item."""

    def __init__(self, candidates: list[PromoCandidate], script: list[Step] | None = None) -> None:
        self.by_id = {c.candidate_id: c for c in candidates}
        self.script = list(script or [])
        self.payloads: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        self.headers.append(request.headers)
        ids = request_ids(payload)
        if self.script:
            return self.script.pop(0)(payload, ids)
        return ok([good_item(self.by_id[i]) for i in ids])

    def client(self, **overrides) -> tuple[OpenRouterClient, list[float]]:
        sleeps: list[float] = []
        transport = httpx.MockTransport(self)
        return OpenRouterClient(settings(**overrides), transport, sleeps.append), sleeps


def request_ids(payload: dict) -> list[str]:
    schema = payload["response_format"]["json_schema"]["schema"]
    return schema["properties"]["evaluations"]["items"]["properties"]["candidate_id"]["enum"]


def user_payload(payload: dict) -> dict:
    """Decode the JSON part of the user message (repair notes, if any, follow it)."""
    content = payload["messages"][1]["content"]
    return json.JSONDecoder().raw_decode(content)[0]
