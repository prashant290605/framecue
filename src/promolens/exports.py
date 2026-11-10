"""Machine-readable deliverables for one run. All files go to a private, gitignored directory.

| File | Contents |
|---|---|
| ``manifest.json`` | Run summary, coverage, cache audit, file list with sha256 |
| ``episodes.json`` | One summary per episode |
| ``candidates.jsonl`` | Every candidate: timestamps, baseline, AI judgement, both ranks |
| ``ai_evaluations.jsonl`` | Validated (derived) AI evaluations |
| ``raw_model_outputs.jsonl`` | Unmodified model responses, when captured |
| ``rankings_ai.json`` | AI-reviewed view: primaries and alternatives |
| ``rankings_coverage.json`` | Full-show baseline view: primaries and alternatives |
| ``ai_recommendations.csv`` | Editor-facing AI-reviewed selections |
| ``coverage_candidates.csv`` | Editor-facing baseline suggestions for every episode |

Everything except ``manifest.json`` is deterministic for a given input, cache and code
version.
"""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

from promolens.pipeline import RunResult, run_summary
from promolens.ranking import PromoFilter, detail, episode_summaries, page
from promolens.results import ExportFile, ExportManifest
from promolens.timecode import format_timestamp

DEFAULT_RAW_DIR = Path("runs/raw")

CSV_COLUMNS = [
    "view", "rank", "alternative_of", "candidate_id", "episode", "start_timestamp",
    "end_timestamp", "start_ms", "end_ms", "duration_seconds", "segments", "score_system",
    "score", "evaluation_status", "hook_score", "cliffhanger_score", "standalone_score",
    "pacing_score", "hook_event_ids", "cliffhanger_event_ids", "hook_explanation",
    "cliffhanger_explanation", "why_it_works", "weakness", "ai_warnings", "crosses_scene",
    "ends_at_episode_end", "model", "prompt_version", "label",
]  # fmt: skip

DESCRIPTIONS = {
    "episodes.json": "Per-episode summary (durations, counts, best picks per view).",
    "candidates.jsonl": "Every shortlisted candidate with timestamps, baseline, AI judgement "
    "where one exists, and ranks in both views.",
    "ai_evaluations.jsonl": "Validated AI evaluations (derived from model output).",
    "raw_model_outputs.jsonl": "Unmodified model responses captured during live runs.",
    "rankings_ai.json": "AI-reviewed ranking view (provisional when coverage is incomplete).",
    "rankings_coverage.json": "Full-show heuristic baseline view (unverified suggestions).",
    "ai_recommendations.csv": "Editor-facing AI-reviewed selections.",
    "coverage_candidates.csv": "Editor-facing baseline suggestions for every episode.",
}


def write_exports(
    run: RunResult, out_dir: Path, raw_dir: Path | None = DEFAULT_RAW_DIR
) -> ExportManifest:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[ExportFile] = []

    def emit(name: str, text: str, records: int) -> None:
        data = text.encode("utf-8")
        (out_dir / name).write_bytes(data)
        files.append(
            ExportFile(
                name=name,
                description=DESCRIPTIONS[name],
                records=records,
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )
        )

    episodes = episode_summaries(run)
    emit("episodes.json", _json([e.model_dump(mode="json") for e in episodes]), len(episodes))

    ordered = sorted(run.by_id.values(), key=lambda c: (c.episode_number, c.start_ms, c.end_ms))
    records = [detail(run, c.candidate_id).model_dump(mode="json", exclude={"timeline"})
               for c in ordered]  # fmt: skip
    emit("candidates.jsonl", _jsonl(records), len(records))

    ai_records = []
    for c in ordered:
        e = run.ai.get(c.candidate_id)
        if e:
            record = e.model_dump(mode="json")
            record["start_timestamp"] = format_timestamp(e.start_ms)
            record["end_timestamp"] = format_timestamp(e.end_ms)
            ai_records.append(record)
    emit("ai_evaluations.jsonl", _jsonl(ai_records), len(ai_records))

    raw = _raw_outputs(run, raw_dir)
    if raw:
        emit("raw_model_outputs.jsonl", _jsonl(raw), len(raw))
        raw_note = f"{len(raw)} unmodified model responses captured for this input's episodes."
    else:
        raw_note = (
            "Not available: raw responses were not captured for the existing cached "
            "evaluations (raw capture was added in Phase 4 and applies to future live runs). "
            "Only validated assessments were cached."
        )

    everything = PromoFilter(include_alternatives=True)
    for view in ("ai", "coverage"):
        full = page(run, view, everything, offset=0, limit=len(run.by_id) or 1)
        emit(f"rankings_{view}.json", full.model_dump_json(indent=1), full.total)
        name = "ai_recommendations.csv" if view == "ai" else "coverage_candidates.csv"
        emit(name, _csv(run, full.items), full.total)

    summary = run_summary(run)
    cov = summary.coverage
    notes = (
        f"AI coverage is {'complete' if cov.complete else 'INCOMPLETE'}: {cov.ai_evaluated} of "
        f"{cov.total_candidates} candidates and {cov.episodes_with_ai} of {cov.total_episodes} "
        "episodes have a valid AI evaluation.",
        "AI scores (ai_weighted_v1, 0-100) and heuristic priorities (heuristic_priority_v1, 0-1) "
        "are different scoring systems and are never ranked together.",
        "Candidates without an AI evaluation carry no AI scores, evidence or explanations.",
        f"Cache entries excluded: {cov.cache.stale_prompt_version} from older prompt versions, "
        f"{cov.cache.other_profile_or_input} from another inference profile or changed input, "
        f"{len(cov.cache.invalid_on_revalidation)} rejected on re-validation.",
        "Times are milliseconds from episode start; timestamps are MM:SS.cc. Clips are "
        "half-open [start, end).",
        f"This run made {summary.live_ai_calls} model API calls.",
    )
    manifest = ExportManifest(
        run=summary,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        files=tuple(files),
        raw_model_outputs=raw_note,
        notes=notes,
    )
    (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
    return manifest


def _raw_outputs(run: RunResult, raw_dir: Path | None) -> list[dict]:
    if raw_dir is None or not Path(raw_dir).is_dir():
        return []
    wanted = {e.source_sha256 for e in run.episodes.values()}
    out = []
    for path in sorted(Path(raw_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("episode_sha256") in wanted:
                out.append(record)
    return out


def _json(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)


def _jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)


def _cell(value) -> str:
    """CSV-safe text: lists joined with ';', and formula-like cells neutralised for Excel."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        value = ";".join(str(v) for v in value)
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _csv(run: RunResult, items) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for p in items:
        e = run.ai.get(p.candidate_id) if p.view == "ai" else None
        s = e.scores if e else None
        row = {
            "view": p.view,
            "rank": p.rank,
            "alternative_of": p.alternative_of,
            "candidate_id": p.candidate_id,
            "episode": p.episode_number,
            "start_timestamp": p.start_timestamp,
            "end_timestamp": p.end_timestamp,
            "start_ms": p.start_ms,
            "end_ms": p.end_ms,
            "duration_seconds": p.duration_seconds,
            "segments": [f"{g.start_timestamp}-{g.end_timestamp}" for g in p.segments],
            "score_system": p.score.system,
            "score": p.score.value,
            "evaluation_status": p.evaluation_status.value,
            "hook_score": s.hook if s else None,
            "cliffhanger_score": s.cliffhanger if s else None,
            "standalone_score": s.standalone if s else None,
            "pacing_score": s.pacing if s else None,
            "hook_event_ids": p.hook_event_ids,
            "cliffhanger_event_ids": p.cliffhanger_event_ids,
            "hook_explanation": p.hook_explanation,
            "cliffhanger_explanation": p.cliffhanger_explanation,
            "why_it_works": p.why_it_works,
            "weakness": p.weakness,
            "ai_warnings": p.warnings,
            "crosses_scene": p.structural.crosses_scene,
            "ends_at_episode_end": p.structural.ends_at_episode_end,
            "model": e.provenance.model_reported if e else None,
            "prompt_version": e.provenance.prompt_version if e else None,
            "label": p.label,
        }
        writer.writerow([_cell(row[c]) for c in CSV_COLUMNS])
    return buffer.getvalue()
