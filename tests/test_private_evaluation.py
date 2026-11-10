"""Prompt construction and validation on the confidential pack. No API calls are made.
Skips unless PROMOLENS_TEST_ZIP is set."""

import json
import os
from pathlib import Path

import pytest

from promolens.candidates import generate_pack_candidates
from promolens.evaluator import validate_batch
from promolens.parser import parse_zip
from promolens.prompts import SYSTEM_PROMPT, build_messages, response_schema
from promolens.schemas import CandidateConfig

ZIP_PATH = os.environ.get("PROMOLENS_TEST_ZIP", "")
CFG = CandidateConfig()
BATCH = 8

pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(
        not ZIP_PATH or not Path(ZIP_PATH).is_file(),
        reason="set PROMOLENS_TEST_ZIP to the private screenplay ZIP",
    ),
]


@pytest.fixture(scope="module")
def batches():
    episodes = parse_zip(ZIP_PATH).raise_for_errors().episodes
    out = []
    for episode, result in zip(episodes, generate_pack_candidates(episodes, CFG), strict=True):
        ordered = sorted(result.shortlist, key=lambda c: (c.start_ms, c.end_ms))
        out += [(episode, ordered[i : i + BATCH]) for i in range(0, len(ordered), BATCH)]
    return out


def test_every_batch_prompt_is_well_formed_and_bounded(batches) -> None:
    sizes = []
    for episode, batch in batches:
        system, user = build_messages(episode, batch, CFG)
        assert system["content"] == SYSTEM_PROMPT
        payload = json.loads(user["content"])
        table = {row[0] for row in payload["events"]}
        for view in payload["candidates"]:
            referenced = {i for k, v in view.items() if isinstance(v, list) for i in v}
            assert referenced <= table  # every id the model sees has its event row
        assert payload["episode"]["episode_number"] == episode.episode_number
        sizes.append(len(user["content"]))
    assert len(batches) == 100  # every episode has 9-16 candidates -> exactly 2 batches of <= 8
    assert max(sizes) < 60_000  # comfortably within context limits


def test_validator_accepts_well_formed_evidence_on_real_events(batches) -> None:
    for episode, batch in batches:
        items = [
            {
                "candidate_id": c.candidate_id,
                "hook_score": 2,
                "cliffhanger_score": 2,
                "standalone_score": 2,
                "pacing_score": 2,
                "hook_event_ids": list(c.opening_event_ids),
                "cliffhanger_event_ids": list(c.ending_event_ids),
                "hook_explanation": "x",
                "cliffhanger_explanation": "x",
                "why_it_works": "x",
                "weakness": "x",
                "warnings": [],
            }
            for c in batch
        ]
        check = validate_batch(episode, batch, {"evaluations": items}, CFG)
        assert set(check.valid) == {c.candidate_id for c in batch}, check.problems
        schema_ids = response_schema([c.candidate_id for c in batch])
        assert schema_ids["properties"]["evaluations"]["items"]["required"]
