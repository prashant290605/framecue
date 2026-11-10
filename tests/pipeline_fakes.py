"""Synthetic packs and an offline way to fill the AI cache (mocked Groq, no network).

All screenplay text is invented. The cache is filled through the real Evaluator and client
code paths, so the pipeline reads exactly what a live run would have written.
"""

import io
import json
import zipfile
from pathlib import Path

import httpx

from framecue.candidates import generate_candidates
from framecue.evaluator import EvaluationCache, EvaluationConfig, Evaluator
from framecue.llm_client import ChatCompletionsClient, LLMSettings
from framecue.schemas import CandidateConfig, Episode
from llm_fakes import TEST_KEY, good_item, ok, request_ids
from synthetic import episode, ev, scene, shots, ts


def episode_text(
    number: int, duration: int = 150_000, line: str = "We should check the list."
) -> str:
    lines = [
        ev("ANNA (Tense)" if i % 2 else "BEN (Cold)", t, t + 2_000, line)
        for i, t in enumerate(range(500, duration - 2_000, 5_000))
    ]
    return episode(number, [scene(1, ts(0), ts(duration), shots(0, duration, 4_000) + lines)])


def pack_bytes(numbers: list[int], extra: dict[str, bytes] | None = None) -> bytes:
    """Byte-identical for identical arguments: entries carry a fixed timestamp, so the
    input checksum never depends on the wall clock (writestr(name) would stamp 'now')."""
    buffer = io.BytesIO()
    members = [(f"pack/episode-{n:02d}.txt", episode_text(n)) for n in numbers]
    members += list((extra or {}).items())
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def write_pack(folder: Path, numbers: list[int]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pack.zip"
    path.write_bytes(pack_bytes(numbers))
    return path


def groq_settings(**overrides) -> LLMSettings:
    return LLMSettings(provider="groq", api_key=TEST_KEY, max_retries=0, **overrides)


def fill_cache(
    cache_dir: Path,
    episodes: list[Episode],
    per_episode: int,
    scores: dict[str, tuple[int, int, int, int]] | None = None,
    settings: LLMSettings | None = None,
) -> list[str]:
    """Evaluate the first ``per_episode`` shortlist candidates of each episode through the
    real Evaluator with a mocked Groq endpoint. ``scores`` maps candidate id to
    (hook, cliffhanger, standalone, pacing); the default is 3 for every dimension."""
    scores = scores or {}
    plan = []
    by_id = {}
    for e in episodes:
        cands = list(generate_candidates(e, CandidateConfig()).shortlist)[:per_episode]
        plan.append((e, cands))
        by_id.update({c.candidate_id: c for c in cands})

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        items = []
        for cid in request_ids(payload):
            h, c, s, p = scores.get(cid, (3, 3, 3, 3))
            items.append(
                good_item(
                    by_id[cid],
                    hook_score=h,
                    cliffhanger_score=c,
                    standalone_score=s,
                    pacing_score=p,
                )
            )
        return ok(items)

    settings = settings or groq_settings()
    client = ChatCompletionsClient(settings, httpx.MockTransport(handler), lambda _: None)
    evaluator = Evaluator(
        client,
        settings,
        EvaluationConfig(batch_size=4),
        CandidateConfig(),
        EvaluationCache(cache_dir),
    )
    run = evaluator.evaluate_run(plan)
    return [e.candidate_id for e in run.evaluations if e.scores is not None]
