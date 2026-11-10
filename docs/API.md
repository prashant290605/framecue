# FrameCue API contract (for the React frontend)

The source of truth is the generated OpenAPI schema at `GET /api/openapi.json` (interactive
docs: `/api/docs`). Generate TypeScript types from it, for example with `openapi-typescript`.
The models live in `src/framecue/document.py` and `src/framecue/results.py`.

## Deployed contract (Vercel, stateless): what the frontend must use

The deployment (`app.py`) exposes only these routes, and they also exist in the local
`framecue serve` app:

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | – | `{status, version, offline: true, live_ai: false, stateless: true, max_upload_bytes}` |
| POST | `/api/analyze` | multipart: `files` (one `.zip`, or `.txt` files) and optional `ai_bundle` (`.json`) | **AnalysisDocument** |

There is no run id and no follow-up request. The frontend keeps the document in memory, so
a refresh needs a re-upload, or a reload of a document the user saved.

**Limits** (Vercel caps request and response bodies at 4.5 MB):
- **Request body:** at most 4.4 MB, and at most 4.0 MB of file content. Check
  `file.size <= max_upload_bytes` in the browser before uploading, because a body over
  4.5 MB is rejected by the platform before our code runs.
- **Archives:** at most 600 ZIP entries, 40 MB decompressed and 200 episodes.
- **Response:** at most 4.3 MB.
- **Over a limit:** the endpoint returns 413 (`upload_too_large`, `too_many_episodes` or
  `analysis_too_large`). The message points to the local CLI, which has no such limits.
- **Measured on the real 50-episode pack:** a 540 KB upload (618 KB with the AI bundle)
  produced a 1.77–1.81 MB response in about 2.3 s.

**AnalysisDocument** (`schema_version: "framecue-analysis-v1"`):
- `summary`: `{analyzed_at, input, episodes, scenes, events, candidates, parse, timings_ms,
  versions, live_ai_calls: 0, ai_source: none|uploaded_bundle|local_cache}`.
- `coverage`: an EvaluationCoverage, including the cache audit.
- `episodes[]`: `{episode_number, source_name, duration_ms, duration_timestamp, logline,
  continuity_notes, scenes[{scene_number, heading, start_ms, end_ms}], event_range[first,
  last), candidate_count, ai_evaluated_count, best_ai_candidate_id,
  top_coverage_candidate_id, parse_warnings}`.
- `events[]`: every screenplay event once, sorted by episode and then time: `{id, episode,
  scene, type, start_ms, end_ms, speaker, annotations[], text}`. An episode's events are
  `events.slice(event_range[0], event_range[1])`.
- `candidates[]`: `{candidate_id, episode_number, segments[], start_ms, end_ms,
  start_timestamp, end_timestamp, duration_ms, evaluation_status, ai_failure_reason,
  baseline{method, priority, components, start_anchors, end_anchors, selection_reason},
  structural{...}, source_events{opening_window[], ending_window[]}, ai_rank,
  ai_alternative_of, coverage_rank, coverage_alternative_of}`. There is no screenplay text
  here.
- `ai_evaluations{candidate_id: AIJudgement}`: genuine, validated model judgements only.
  It is empty unless an `ai_bundle` was uploaded.
- `views.ai` and `views.coverage`: `{view, scoring_system, score_scale_max, label,
  provisional, provisional_reason, primaries, order[]}`. `order` lists every member in
  display order, each primary followed by its alternatives.
- `notes[]`.

**Building the UI from the document (in memory):**
- **AI-reviewed list:** `views.ai.order`, keeping ids whose `ai_rank` is not null, joined
  with `ai_evaluations` for scores and explanations.
- **Full-show list:** `views.coverage.order`, keeping those whose `coverage_rank` is not null,
  scored with `baseline.priority`. Never show AI fields in this list.
- **Filters:** episode, duration, score within the list's own system, `evaluation_status`,
  and AI `warnings`, all applied client-side. Ranks stay the original ones.
- **Timeline:** take the episode's event slice and keep events with
  `start_ms < clip_end + 10 s` and `end_ms > clip_start - 10 s`. An event is in the clip when
  `start < end_ms && end > start_ms` (a zero-length event at t: `start_ms <= t < end_ms`).
  The hook window is the first 3 s and the ending window the last 5 s. Roles come from
  `source_events` and the AI `hook_event_ids` / `cliffhanger_event_ids`.
- **Exports:** the document itself is the JSON export. Build the CSVs client-side with the
  columns of the local `ai_recommendations.csv` / `coverage_candidates.csv` (listed in
  `src/framecue/exports.py`). Prefix cells starting with `= + - @` with `'`.

**Private AI results online, without deploying them.** Run `framecue export-ai` locally
(it writes `runs/ai_bundle.json`) and upload that file as `ai_bundle` together with the
screenplay.
- **Validation:** the server uses an entry only if its cache key matches the uploaded
  screenplay exactly (file checksum, window, prompt, model profile) and its evidence
  re-validates. Everything else is counted in `coverage.cache` and ignored.
- **Privacy:** the bundle contains model explanations about the scripts, so keep it private.
  It is processed for that request only.

## Local-only stateful API (`framecue serve`)

The routes below use a run store on local disk. They are **not deployed**: a Vercel
function's filesystem is read-only apart from `/tmp`, and requests can land on different
instances. They stay for local development and tests.

Start locally (binds to 127.0.0.1 only):

```bash
.venv\Scripts\framecue serve --port 8000
```

CORS allows only `http://localhost:5173` and `http://127.0.0.1:5173` (Vite's defaults). To
override, set `FRAMECUE_CORS_ORIGINS` (comma-separated). No endpoint calls a model: the API
is offline and reuses cached AI evaluations only.

## Conventions

- **Times:** `*_ms` fields are integer milliseconds from the episode start. `*_timestamp`
  fields are `MM:SS.cc`. Clips are half-open `[start, end)`.
- **Segments:** `segments` is ordered. v1 always has exactly one segment, but the UI should
  render a list.
- **IDs:**
  - candidate: `C<episode:3>-<start_ms:7>-<end_ms:7>`;
  - event: `E<episode:3>-S<scene:2>-<index:4>`;
  - run: 22 URL-safe characters, random and unguessable. Runs are never listed.
- **Errors:** always `{"error": {"code": string, "message": string, ...extra}}`.
  - 422 validation errors add `fields: [{loc, type}]` and never echo input values.
  - Codes:
    - 400: `no_files`, `too_many_files`, `unsupported_file_type`, `mixed_inputs`,
      `not_a_zip`;
    - 404: `run_not_found`, `candidate_not_found`, `export_not_found`, `not_found`;
    - 413: `upload_too_large`;
    - 422: `no_valid_episodes` (with `issue_codes`), `invalid_request`;
    - 500: `internal_error`.
- **Headers:** every response carries `Cache-Control: no-store`.

## Two scoring systems; never compare them

| `score.system` | Range | Where | Meaning |
|---|---|---|---|
| `ai_weighted_v1` | 0–100 | `view=ai` rows, `PromoDetail.ai` | Weighted rubric from a real, validated model evaluation |
| `heuristic_priority_v1` | 0–1 | `view=coverage` rows, `PromoDetail.baseline` | Deterministic baseline ordering; **unverified** |

Show `score.label` next to every score.

- **`evaluation_status`:** `ai_evaluated`, `not_evaluated` (baseline only) or `failed` (a
  live run tried and could not get a valid evaluation).
- **`view=coverage` rows** never contain AI explanations or evidence. `ai_score_available`
  only says that an AI review exists in the AI view and the detail.

## Endpoints

### `GET /api/health`
Returns `{status: "ok", version, offline: true, live_ai: false}`.

### `POST /api/runs`, multipart field `files`
Upload exactly one `.zip`, or one or more `.txt` episode files.
- **Limits:** 25 MB total, 120 files.
- **ZIP safety:** ZIPs are parsed in memory with the parser's safety rules (no traversal,
  symlinks, encryption, oversized or duplicate entries; 100 MB decompressed cap).
- **Response:** `201` with **RunSummary**:
  - `run_id`, `status` (`completed`), `created_at`;
  - `input {name, kind: zip|txt|directory|files, sha256, size_bytes, files}`;
  - `episodes`, `scenes`, `events`, `candidates`;
  - `parse {errors, warnings, info, excluded_sources[], issue_codes{}}`;
  - `coverage` (an EvaluationCoverage), `timings_ms{}`, `versions{}`;
  - `live_ai_calls` (always 0).

### `GET /api/runs/{run_id}`
Returns the same **RunSummary**.

### `GET /api/runs/{run_id}/coverage`
Returns **EvaluationCoverage**:
- **Totals:** `total_episodes`, `total_candidates`.
- **Status counts:** `ai_evaluated`, `not_evaluated`, `failed`.
- **Episode coverage:** `episodes_with_ai`, `episodes_without_ai[]`.
- **Ratios:** `candidate_coverage`, `episode_coverage` (0–1), and `complete`.
- **`profile`:** `{provider, model, reasoning_effort, prompt_version, scoring_rules}`.
- **`cache`:** `{entries_scanned, used, invalid_on_revalidation[], stale_prompt_version,
  other_profile_or_input, not_in_this_input, stale_prompt_versions_seen[]}`.

### `GET /api/runs/{run_id}/episodes`
Returns **EpisodeSummary[]**, sorted by episode:
- `episode_number`, `source_name`, `duration_ms`, `duration_timestamp`;
- `scene_count`, `event_count`, `candidate_count`, `ai_evaluated_count`;
- `best_ai_candidate_id`, `best_ai_score`, `best_ai_rank`;
- `top_coverage_candidate_id`, `top_coverage_rank`, `parse_warnings`.

### `GET /api/runs/{run_id}/promos`
Returns **PromoPage**: `{view, scoring_system, provisional, provisional_reason, total,
offset, limit, items: RankedPromo[]}`.

| Query | Type | Notes |
|---|---|---|
| `view` | `ai` \| `coverage` | default `ai` |
| `episode` | int, repeatable | `?episode=3&episode=7` |
| `min_duration`, `max_duration` | seconds | 0–600 |
| `min_score` | number | in the view's own system: 0–100 for `ai`, 0–1 for `coverage` |
| `status` | repeatable | `ai_evaluated`, `not_evaluated`, `failed` |
| `warning`, `exclude_warning` | repeatable | AI warning codes, for example `weak_ending` |
| `include_alternatives` | bool | default false: primaries only |
| `limit`, `offset` | int | `limit` 1–200 (default 50) |

`total` counts matches. `rank` is the position in the **full** view, so filtered lists keep
their original ranks. Alternatives have `rank: null` and `alternative_of: <primary id>`.

**RankedPromo** fields:
- **Position:** `view`, `rank`, `alternative_of`, `alternatives[]`.
- **Clip:** `candidate_id`, `episode_number`, `segments[{index, start_ms, end_ms,
  start_timestamp, end_timestamp, duration_ms}]`, `start_ms`, `end_ms`, `start_timestamp`,
  `end_timestamp`, `duration_ms`, `duration_seconds`.
- **Score:** `score {system, value, scale_max, label}`, `evaluation_status`,
  `ai_score_available`.
- **AI fields** (AI view only; otherwise empty or null): `hook_event_ids[]`,
  `cliffhanger_event_ids[]`, `hook_explanation`, `cliffhanger_explanation`, `why_it_works`,
  `weakness`, `warnings[]`.
- **Structure:** `structural {crosses_scene, starts_at_scene_start, ends_at_scene_end,
  ends_at_episode_end, clean_start_cut, clean_end_cut}`, and
  `source_events {opening_window[], ending_window[]}` (structural windows, not AI evidence).
- **`label`:** the provenance text to display.

### `GET /api/runs/{run_id}/promos/{candidate_id}`
Returns **PromoDetail**:
- **Clip and status:** the clip fields above, plus `evaluation_status`.
- **AI:** `ai` (an **AIJudgement** or `null`) and `ai_failure_reason`.
  - Scores: `weighted_score`, `hook_score`, `cliffhanger_score`, `standalone_score`,
    `pacing_score`, `rubric_weights`.
  - Evidence: `hook_event_ids`, `cliffhanger_event_ids`.
  - Explanations: `hook_explanation`, `cliffhanger_explanation`, `why_it_works`,
    `weakness`, `warnings`.
  - Provenance: `model_requested`, `model_reported`, `provider`, `prompt_version`,
    `cache_key`.
- **Baseline:** `baseline {method, priority, components{}, start_anchors[], end_anchors[],
  selection_reason}`.
- **Ranks:** `ai_rank`, `ai_alternative_of`, `coverage_rank`, `coverage_alternative_of`.
- **Structure:** `structural` and `source_events`.
- **`timeline`:**
  - `{episode_number, episode_duration_ms, window_start_ms, window_end_ms, clip_start_ms,
    clip_end_ms, hook_window[2], ending_window[2], scenes[{scene_number, heading, start_ms,
    end_ms}], events[TimelineEvent]}`;
  - the window is the clip ±10 s.

**TimelineEvent** fields:
- `event_id`, `event_type` (`CAMERA`, `DIALOGUE`, `ACTION` or `SOUND_FX`), `scene_number`;
- `start_ms`, `end_ms`, `start_timestamp`, `end_timestamp`;
- `speaker`, `annotations[]`, `text`;
- `in_clip`, `in_hook_window`, `in_ending_window`, `cut_at_start`, `cut_at_end`;
- `roles[]`: `ai_hook_evidence`, `ai_cliffhanger_evidence`, `opening_window` or
  `ending_window`.

`text` is screenplay content. Render it only inside the local UI, and never log it.

### `GET /api/runs/{run_id}/export`
Returns **ExportManifest**: `{run: RunSummary, generated_at, files[{name, description, records,
sha256, bytes}], raw_model_outputs, notes[]}`.

### `GET /api/runs/{run_id}/export/{file_name}`
Downloads one file from a fixed allowlist: `manifest.json`, `episodes.json`,
`candidates.jsonl`, `ai_evaluations.jsonl`, `raw_model_outputs.jsonl` (only if captured),
`rankings_ai.json`, `rankings_coverage.json`, `ai_recommendations.csv`,
`coverage_candidates.csv`.

## Serving and routing on Vercel (verified)

- **Static files** (`/`, `/assets/*`, `/sample/*`, `/favicon.svg`) come from the CDN and
  never invoke Python.
- **Navigation requests** to any other path (for example `/review`) return `index.html`, so
  the SPA handles its own routes. Non-navigation requests to unknown paths get 404.
- **`/api/*`** always reaches the function. Unknown API paths get a JSON 404, never HTML.

## Phase 5 integration notes (implemented in `frontend/`)

The frontend uses **only** `/api/health` and `POST /api/analyze`. That way it works
identically on Vercel and against `framecue serve`.

- **Upload:**
  1. Read `max_upload_bytes` from `/api/health`, and block larger files client-side with the
     CLI hint.
  2. POST `files`, plus an optional `ai_bundle`.
  3. Show progress for about 2–3 s on the full pack.
- **State:** keep the `AnalysisDocument` in memory, for example in a React context or a
  store. Offer "Save analysis (JSON)", and accept a saved document back so a refresh does
  not lose work. This is optional and fully client-side; validate `schema_version` on load.
  Saved documents contain screenplay text, so say so in the UI.
- **Screens:**
  - an AI-reviewed tab: `views.ai`, with a provisional banner from `provisional_reason`, and
    an empty state that explains how to upload an AI bundle;
  - a full-show tab: `views.coverage`, with the "unverified" `label`;
  - a coverage panel: `coverage` plus `episodes`;
  - a detail drawer: a timeline built from the episode's event slice (see above).
- **Score display:** never put the two score systems in one sortable column. Use the view
  `label` and `score_scale_max`.
- **Alternatives:** show `*_alternative_of` members as "alternative cuts" under each primary.
- **Accept/reject:** editor decisions stay client-side (memory or a downloaded JSON). There
  is no server persistence.
- **Types:** generate them from `/api/openapi.json` of the stateless app (`AnalysisDocument`
  and its parts).
