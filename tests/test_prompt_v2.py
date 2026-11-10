"""Prompt v2: compact rows and short event aliases. Validation must still run on real ids."""

import json

from framecue.candidates import generate_candidates
from framecue.prompts import alias_maps, build_messages, to_real_ids
from framecue.schemas import CandidateConfig, EvaluationStatus
from llm_fakes import FakeOpenRouter, good_item, ok, user_payload
from test_evaluator import make_episode, run


def setup():
    episode = make_episode()
    cands = list(generate_candidates(episode).shortlist)
    _, to_alias = alias_maps(episode)
    return episode, cands, to_alias


def aliased_item(c, to_alias, **overrides):
    item = good_item(c)
    item["hook_event_ids"] = [to_alias[i] for i in item["hook_event_ids"]]
    item["cliffhanger_event_ids"] = [to_alias[i] for i in item["cliffhanger_event_ids"]]
    item["hook_explanation"] = f"{to_alias[c.opening_event_ids[0]]} lands at once."
    return {**item, **overrides}


def test_payload_uses_aliases_rows_and_no_real_ids() -> None:
    episode, cands, to_alias = setup()
    user = build_messages(episode, cands[:3], CandidateConfig())[1]["content"]
    payload = json.loads(user)
    assert all(isinstance(r, list) and len(r) == 6 for r in payload["events"])
    assert {r[1] for r in payload["events"]} <= {"CAM", "DLG", "ACT", "SFX"}
    assert "E001-S01-" not in user  # real ids never reach the model
    table = {r[0] for r in payload["events"]}
    for view in payload["candidates"]:
        assert set(view["first_3s"]) <= set(view["clip"]) and set(view["clip"]) <= table


def test_aliased_answer_is_mapped_to_real_ids_and_accepted() -> None:
    episode, cands, to_alias = setup()
    c = cands[0]
    fake = FakeOpenRouter(cands, [lambda p, ids: ok([aliased_item(c, to_alias)])])
    e = run(episode, [c], fake).evaluations[0]
    assert e.status is EvaluationStatus.LLM_EVALUATED
    assert e.hook_event_ids == (c.opening_event_ids[0],)
    assert e.cliffhanger_event_ids == (c.ending_event_ids[-1],)
    assert c.opening_event_ids[0] in e.hook_explanation  # alias replaced in prose too


def test_unknown_or_out_of_window_alias_is_still_rejected() -> None:
    episode, cands, to_alias = setup()
    c = cands[0]
    late = to_alias[c.ending_event_ids[-1]]  # exists, but not in the first 3 s
    bad = [
        aliased_item(c, to_alias, hook_event_ids=["e99999"]),
        aliased_item(c, to_alias, hook_event_ids=[late]),
    ]
    for item in bad:
        fake = FakeOpenRouter(cands, [lambda p, ids, item=item: ok([item])] * 3)
        e = run(episode, [c], fake).evaluations[0]
        assert e.status is EvaluationStatus.HEURISTIC_FALLBACK and e.scores is None
        # repair notes reach the model in alias form, never as real ids
        notes = fake.payloads[1]["messages"][1]["content"].split("failed validation")[1]
        assert "E001-S01-" not in notes and "hook_event_ids" in notes


def test_to_real_ids_passes_malformed_shapes_through() -> None:
    to_real = {"e1": "E001-S01-0001"}
    assert to_real_ids([1, 2], to_real) == [1, 2]
    assert to_real_ids({"evaluations": "x"}, to_real) == {"evaluations": "x"}
    mixed = to_real_ids({"evaluations": [7, {"hook_event_ids": ["e1", 3, "e2"]}]}, to_real)
    assert mixed["evaluations"] == [7, {"hook_event_ids": ["E001-S01-0001", 3, "e2"]}]


def test_user_payload_helper_reads_v2() -> None:
    episode, cands, _ = setup()
    fake = FakeOpenRouter(cands)
    run(episode, cands[:2], fake)
    assert [v["candidate_id"] for v in user_payload(fake.payloads[0])["candidates"]]


def test_validation_log_records_problems_without_script_text() -> None:
    from framecue.evaluator import EvaluationConfig, Evaluator
    from llm_fakes import settings

    episode, cands, to_alias = setup()
    c = cands[0]
    bad = aliased_item(c, to_alias, hook_event_ids=["e99999"])
    fake = FakeOpenRouter(cands, [lambda p, ids: ok([bad])])
    client, _ = fake.client()
    evaluator = Evaluator(client, settings(), EvaluationConfig(), CandidateConfig())
    result = evaluator.evaluate_run([(episode, [c])])
    assert result.evaluations[0].status is EvaluationStatus.LLM_EVALUATED  # repaired on retry
    assert len(evaluator.validation_log) == 1
    entry = evaluator.validation_log[0]
    assert entry.startswith("attempt 1: ") and c.candidate_id in entry and "e99999" in entry
    texts = {e.text for e in episode.events}
    assert not any(t in entry for t in texts)
