# FrameCue: architecture contract

Status: Phases 0–4 implemented. Phase 3.5 validated the evaluator with **real inference** on
GroqCloud's Free Plan (`openai/gpt-oss-120b`, $0), as documented in §10b. Phase 4 added the
offline full-pack pipeline, two separate ranking views, exports, the CLI and the FastAPI
backend (§15), a stateless Vercel deployment path (§16), and the React editorial workspace deployed on
Vercel (§17). AI coverage is 41 of 788 candidates in
12 of 50 episodes, so the AI view is provisional. This document is the spec that later phases build against. Change it
deliberately, and record why in `docs/AI_LOG.md`.

## 0. Product architecture (updated in Phase 3: no Streamlit)

```
React + TypeScript + Vite, Tailwind CSS + shadcn/ui     (Phase 5: editor review UI)
                 │  JSON over HTTP (docs/API.md)
FastAPI backend: api.py                                  (Phase 4: thin routes, offline)
                 │  Python calls
FrameCue core (this package)
   parser ─► candidates + baseline ─► cached-AI lookup ─► ranking ─► exports   (pipeline.py)
                                            ▲
   opt-in live path: eval_driver ─► evaluator ─► llm_client (GroqCloud free / OpenRouter)
                                            └── writes the AI cache only
```

- All domain logic lives in the core package, so the API and UI stay thin and testable.
- Streamlit is no longer used.
- No database: runs and the evaluation cache are JSON files under the gitignored `runs/`.

## 1. Problem

Kuku supplies timestamped microdrama screenplays (50 episodes for one show). FrameCue finds and
ranks 20–60 s promotional clips. Each recommendation states: episode, exact start/end of every
segment (in order), the opening hook, the cliffhanger, and one line on why it works. Clips must make
sense to a viewer who has never seen the show. The prototype must run again on a new input pack
during the interview, with a swappable model.

## 2. User workflow (editor)

1. Drop a screenplay pack (ZIP or folder of episode `.txt` files) into `data/private/`, or upload
   it in the web UI.
2. Run `framecue run --input <path> [--evaluator baseline|llm]`. Output goes to
   `runs/<run_id>/` (gitignored).
3. Read the parse report first: episodes, scenes, events, warnings. A pack that fails to parse
   stops the run with a clear error; it never yields silently partial results.
4. Review the ranked promo list: timestamps, hook, cliffhanger, reason, sub-scores, and which
   evaluator produced them (baseline or a named model; fallback is labelled).
5. Accept, reject or adjust in the web UI. Export JSON/CSV for the editing team.

## 3. Scope

In scope (v1):
- Parsing the verified input format (section 4) into typed models, with a validation report.
- Continuous single-segment clips inside one episode. A clip may cross a scene boundary within
  the episode, because the episode timeline is continuous.
- Deterministic candidate generation, boundary validation and a deterministic baseline scorer.
- An optional LLM evaluator for subjective editorial judgement, with strict output validation.
- Ranking with diversity rules, CLI batch runs, a React/FastAPI review UI, and run manifests.
- Quality analysis against human labels, including honest failure cases.

Out of scope (v1):
- Cross-episode stitching and automatic multi-segment montage. The `segments` list exists so
  this can be added later without a schema change.
- Video, audio, editing, music, captions or overlays.
- Databases, queues, auth, microservices, or any deployment beyond one FastAPI app and one static
  frontend.
- Fine-tuning or training models.

## 4. Input format (verified by a read-only audit in Phase 0)

Counts below come from a script run over the actual pack. No script content is reproduced here.

| Property | Finding |
|---|---|
| Files | 50 `.txt` files in one folder inside the ZIP, named `episode-NNN-Episode <numeric id>.txt`. ZIP entries are stored (uncompressed). |
| Encoding | UTF-8 in all files, no BOM, LF line endings. Non-ASCII: U+2013 (86), U+2014 (14), U+00E9 (1). **Must read with `encoding="utf-8"`.** The Windows default (cp1252) would garble these characters. |
| Header | `EPISODE NUMBER: Episode N`, `EPISODE LOGLINE: …`, `CONTINUITY NOTES: …` in all 50 files. Episode numbers are 1–50, contiguous, and match the filename numbers. |
| Body markers | Exactly one `--- SCRIPT START ---` and one `--- SCRIPT END ---` per file. |
| Scenes | 100 scenes (1–5 per episode, mean 2.0). Each has `SCENE NN`, `Scene heading:` (INT./EXT. slugline with an en dash before the time of day), `Scene start:`, `Scene end:` and `Actions and dialogue:`. |
| Scene timing | Episode-relative. Every episode starts at `00:00.00`. Scene N+1 starts exactly where scene N ends, with no gaps or overlaps. Scene lengths run from 2.3 s to 200.75 s; 14 scenes are shorter than 20 s. |
| Episode length | 70.5 s to 200.8 s (median about 119 s, total about 103 min). Many episodes run longer than the brief's "1–2 minutes". |
| Event blocks | 3,727 blocks. Each is a `**LABEL:**` line, then `Start: (MM:SS.cc) \| End: (MM:SS.cc)`, then exactly one text line. Counts: CAMERA 1,754, DIALOGUE 1,487, ACTION 372, SOUND FX 114. |
| Dialogue labels | `**SPEAKER (Delivery note):**`. All 1,487 have a parenthetical delivery note. Some add `(O.S.)` or `(V.O.)` before it. Every dialogue line is in double quotes. There are 64 distinct speaker labels. |
| Timestamps | All 7,654 timestamps use the form `MM:SS.cc` (2+2+2 digits); the largest minute value is 3. 7,584 sit on a 0.1 s grid. The other 70 use full centisecond precision. |

Irregularities the parser must handle:
1. **Case variance:** in one episode the scene metadata lines are uppercase (`SCENE HEADING:`,
   `SCENE START:`, `SCENE END:`, `ACTIONS AND DIALOGUE:`). Match these keys case-insensitively.
2. **Out-of-order events:** in 8 places an event's start is earlier than the previous block's
   start, by up to 1.0 s. Sort by `(start_ms, source_index)` and keep `source_index`.
3. **CAMERA coverage is not continuous:** there are 112 gaps between consecutive shots (median 2.0 s,
   max 25.5 s), and ACTION blocks fill them. Across the union of all event types, 52 small
   uncovered gaps remain (max 2.0 s). Do not treat CAMERA as the only visual timeline.
4. **Overlaps:** 5 CAMERA/CAMERA overlaps (max 1.8 s) and 10 DIALOGUE/DIALOGUE overlaps
   (max 0.6 s). The Phase 1 parser also found 4 ACTION/ACTION overlaps and 1 SOUND FX/SOUND FX
   overlap, for 20 in total. All are reported as INFO and preserved.
5. **Boundary spill:** one SOUND FX block ends 0.3 s after its scene ends. Camera coverage starts
   late in 3 scenes (max 3.0 s) and ends early in 9 scenes (max 9.7 s).
6. **Speaker aliasing:** a single character can appear under several labels, such as with or
   without an honorific, or under a descriptive label before the name is revealed. This affects
   standalone-clarity checks and must not be "fixed" silently. Phase 1 adds an optional, explicit
   alias map.
7. **Truncated last shot:** episodes often end on a short "cut to black" camera block and a
   rising SOUND FX cue. These are useful cliffhanger signals.

## 5. Time representation

- All times are **integer milliseconds** (`int`, ≥ 0). Source `MM:SS.cc` converts exactly to
  `(MM*60 + SS)*1000 + cc*10`. No floats appear anywhere in timing logic.
- Formatting back to text uses `MM:SS.cc` when the value is a multiple of 10 ms, which is always
  true for this pack. Otherwise it uses `MM:SS.mmm`.
- Durations, overlaps and the 20–60 s window are integer comparisons:
  `20_000 <= duration_ms <= 60_000`.
- Milliseconds rather than centiseconds, so future inputs (SRT, EDL, video) fit without a change.

## 6. Data models (Pydantic v2)

Models are frozen, reject unknown fields, use tuples for collections and validate their own
invariants. They round-trip through JSON.

Implemented in Phase 1 (`src/framecue/schemas.py`):
- **ScriptEvent** has these fields:
  - `event_id`: `E{episode:03d}-S{scene:02d}-{source_index:04d}`, for example
    `E001-S01-0007`. The index is zero-based and counts across the whole episode, so IDs stay
    stable and unique.
  - `episode_number`, `scene_number`, `source_index`, `source_line`.
  - `event_type`: one of `CAMERA`, `DIALOGUE`, `ACTION`, `SOUND_FX`.
  - `label`: the original label, verbatim.
  - `speaker` and `annotations`: dialogue only. `annotations` keeps every parenthetical in
    source order, such as `("O.S.", "Calm")`.
  - `text` (verbatim; multiple lines are joined with `\n`), `start_ms`, `end_ms`.

  Invariants: `end_ms >= start_ms`, and only dialogue events have a speaker.

  *Deviation from the Phase 0 draft:* the draft split annotations into `delivery` and
  `modifiers`. They are now kept raw, so nothing is normalized away. Normalization can come
  later.
- **Scene** fields: `episode_number`, `scene_number`, `heading`, `start_ms`, `end_ms`,
  `source_line`, `events` (source order) and `unparsed_lines`. Invariant: end > start.
- **Episode** fields:
  - `episode_number`, `source_name`, `source_sha256`, `source_id` (from the filename).
  - `logline`, `continuity_notes`, `extra_metadata` (header keys the parser does not know).
  - `scenes`, `unparsed_lines`.

  Properties: `duration_ms`, `events` (source order), `timeline()` (sorted by
  `(start_ms, source_index)`).
- **ParseIssue** fields: `severity`, `code`, `message`, `source`, `line`, `episode_number`,
  `scene_number`, `event_id`. Severities:
  - `ERROR`: the episode is excluded.
  - `WARNING`: a source anomaly, kept as written.
  - `INFO`: legitimate structure, such as two camera shots that overlap.

  Messages never quote script text.
- **ParseResult** fields: `source`, `episodes` (only those with no errors), `issues`. It also
  provides `ok`, `errors`, `counts()`, `issue_counts()` and `raise_for_errors()`.

Implemented in Phase 2 (`schemas.py`). Clips are half-open intervals `[start_ms, end_ms)`.
- **ClipSegment**: `episode_number`, `start_ms`, `end_ms`. Invariant: start < end.
- **PromoCandidate** fields:
  - `candidate_id`, which is `C{episode:03d}-{start:07d}-{end:07d}`. It is readable and
    deterministic, with one id per window.
  - `segments` (exactly one in v1), `start_ms`, `end_ms`, `duration_ms`.
  - `start_anchors` and `end_anchors`: why each boundary was chosen.
  - `opening_event_ids` and `ending_event_ids`: the events intersecting the first 3 s and the
    last 5 s.
  - `signals`: a `CandidateSignals` model of structural facts and cue categories.
  - `heuristic`: a `HeuristicAssessment` carrying `method`, `priority` and `components`.
  - `selection_reason`.

  The model checks that segments are consistent, the id matches the window, and the evidence
  ids belong to the candidate's episode.

  *Deviation:* the id is readable rather than a hash, and the segments carry no event-id list.
  Evidence ids sit on the candidate.
- **CandidateConfig** and **BaselineWeights** hold every tunable, with validated ranges.
- **EpisodeCandidates** holds the shortlist plus the anchor, raw-window and pool counts.

Planned for later phases:
- **PromoEvaluation**: `candidate_id`, `evaluator` (`baseline` or `llm:<provider>/<model>`),
  `scores` with `hook`, `cliffhanger`, `standalone`, `pacing` as ints 0–10, `total` (computed,
  never model-supplied), `hook_text`, `cliffhanger_text`, `why` (≤ 200 chars),
  `hook_event_ids`, `cliffhanger_event_ids`, `status: ok|invalid|failed|fallback`, `errors`,
  `prompt_version`, `response_ref` (cache key, not raw text).
- **RunManifest**: `run_id`, `created_at`, `package_version`, `git_commit` (and whether the tree is
  dirty), `input_files` (name, size, sha256; no content), `config` (secrets removed), `evaluator`,
  `model`, `temperature`, `prompt_version`, counts (episodes, events, candidates, evaluated),
  LLM call stats (attempted, succeeded, invalid, failed, fell back), timings, warnings.

## 7. Candidate generation (deterministic, implemented in Phase 2)

The goal is **recall**: excellent moments should rarely be lost before the LLM sees them. The
code is in `candidates.py` and `baseline.py`.

1. **Anchors.**
   - *Start anchors:*
     - the episode start and every scene start;
     - every event onset: camera cut, dialogue, action or sound.
   - *End anchors:*
     - the episode end and every scene end;
     - every event end;
     - `before_line`, the instant just before each dialogue line. It becomes `before_answer`
       when the previous line was a question.
     - `open_line_end`, the end of a line ending in "?" or trailing off;
     - `shot_end_after_line`, a camera cut no more than 1.5 s after the latest line ends.
   - Anchors carry weak cue tags (`cue:question`, `cue:physical_action` and so on).
   - Any anchor that falls inside a dialogue line is dropped, so **no cut ever splits speech**.
     Cutting through camera, action or sound events is allowed, but it is measured.
   - *Fallback grid* anchors are placed every 5 s wherever no real anchor lies within 2.5 s. They
     keep sparse stretches reachable and are labelled `fallback_grid`.
2. **Raw windows.** Every anchor pair with `20 s <= duration <= 60 s` is counted with bisect.
   These windows are counted, never built.
3. **Pool (bounded).** For each start, the 20–60 s range is split into 4 duration bands. The pool
   keeps the 2 best ends per band, ranked by end-anchor quality plus duration preference, and
   always keeps the episode end. That caps the pool at 9 windows per start.
4. **Scoring.** Every pool window gets a heuristic priority (§8a).
5. **Shortlist** (default 16 per episode, in stable priority order):
   1. Seeds: the best window in each third of the episode, plus the best window ending at the
      episode end. Episodes are written to end on a cliffhanger, so this seed is structural, not
      lexical.
   2. Distinct moments only, where IoU < 0.5 with every chosen window, up to 16 − 4 slots.
   3. Distinct moments, plus up to 2 *alternative cuts* per moment.
   4. A fill pass, which allows more alternatives when slots remain (short episodes).

   An alternative must overlap exactly one moment and move its start or end by at least 2 s.
   This is how near-duplicates are removed without losing every alternative boundary for a
   strong moment.

**The recall vs. shortlist-size trade-off.**
- A larger shortlist raises the chance that the best cut survives, but it costs more LLM tokens
  and editor attention.
- 16 per episode is about 800 candidates per pack, which is roughly 50 per-episode LLM calls.
  That is an engineering default, not a measured optimum.
- Because the pool is kept and inspectable (`generate_pool`), recall at different shortlist sizes
  can be measured once human labels exist (Phase 5).

**Validation** (`validate_candidates`, run on every generated shortlist) checks that:
- each duration lies in the configured range;
- each window lies within `[0, episode_duration]`;
- every boundary equals a real source timing, unless it is labelled as a fallback anchor;
- every evidence id exists and equals the recomputed half-open evidence windows;
- no window is duplicated.

**Measured on the real pack (Phase 2, default config).** These are structural facts, not
quality claims.

| Measure | Result |
|---|---|
| Raw windows | 65,362 |
| Pool | 18,726 windows (148–777 per episode) |
| Shortlist | 788 candidates (min 9, median 16, max 16 per episode) |
| Duration | 20.0 s / 25.8 s / 31.7 s / 40.4 s / 60.0 s (min / p25 / median / p75 / max) |
| Timeline coverage by shortlist | min 94%, median 100%; every third of every episode touched |
| Scene-crossing clips | 178 |
| Dialogue-boundary conflicts | 0 |
| Duplicates | 0 |
| Clean camera cut at start / end | 705 / 705 |
| Episodes ending in the shortlist | 50 / 50 |
| Runtime | about 2.0 s for the whole pack (parse 0.09 s) |

## 8a. Heuristic baseline (implemented in Phase 2)

`baseline.assess` returns a `HeuristicAssessment` with `method="heuristic_baseline_v1"`. It is a
**heuristic priority**, not a promo-quality score and not an AI judgement. Each component is in
[0, 1], and the priority is their weighted mean, with configurable weights:

| Component | Default weight | What it actually measures |
|---|---|---|
| opening | 0.25 | An onset in the first 3 s, cue categories there, a "?" or "!" line. |
| ending | 0.25 | A final line that is open ("?" or trailing off), a cut just before a line or at the episode end, a strong cue in the last 5 s, a scene or episode boundary. |
| activity | 0.15 | Lines and beats per 10 s, minus a penalty for dead air longer than 4 s. |
| boundary | 0.15 | Clean camera cuts at both ends and few events sliced. Halved if dialogue were cut. |
| duration | 0.10 | 1.0 for 30–45 s, falling linearly to 0.5 at 20 s and at 60 s. |
| context | 0.10 | Two or more speakers, three or more lines, a start at a scene opening (a weak standalone proxy). |

The cue categories come from punctuation, dialogue delivery notes and short lists of generic
English stems:
- `question`, `exclamation`, `trailing_off`;
- `intense_delivery`;
- `physical_action`, which is matched in camera and action text only;
- `reveal_cue`, `sound_fx`.

They change the ordering only. They never create or remove windows. The baseline does not
understand story meaning. It is **not** the §8 rubric. When an LLM evaluation fails, the
fallback carries only this heuristic priority, labelled `heuristic_fallback`, and **no** rubric
scores. No component-to-rubric mapping was invented.

## 8. Evaluation rubric (implemented in Phase 3)

The model gives integers from 0 to 5. Python computes
`weighted_score = 100 * Σ(wᵢ·sᵢ) / (5·Σwᵢ)`, rounded to 2 decimals, which lies in 0–100. Weights
come from the configurable `RubricWeights`. The scores are **editorial estimates, not conversion
probabilities**. No confidence values are produced.

| Criterion | Weight | Window judged | Anchors |
|---|---|---|---|
| Hook | 30% | first 3 s | 0 = nothing would stop a scroll; 1 = the hook comes after 3 s; 2 = generic tension; 3 = a clear, legible dramatic beat; 4 = a shocking line, physical action or reveal; 5 = an unmistakable scroll-stopper (reserved) |
| Cliffhanger | 30% | final 5 s and the cut | 0 = resolution or fade-out; 1 = the scene just stops; 2 = a low-stakes open thread, or one partly answered; 3 = a clear open question or threat; 4 = the cut lands right before a decisive answer or action; 5 = an urgent, high-stakes moment (reserved). A question resolved inside the clip scores at most 2. |
| Standalone | 25% | the whole clip | 0 = incomprehensible; 1 = depends heavily on prior knowledge; 2 = gist only; 3 = conflict and stakes clear, with minor gaps; 4 = self-contained; 5 = fully self-explanatory |
| Pacing | 15% | the whole clip | 0 = dead air; 1 = slow; 2 = uneven; 3 = steady; 4 = tight escalation; 5 = exceptionally tight |

Each evaluation also carries:
- hook and cliffhanger evidence event ids;
- four short explanations: hook, cliffhanger, why it works, weakness;
- `QualityWarning` codes: `no_clear_hook`, `hook_too_late`, `weak_ending`,
  `resolved_before_cut`, `needs_prior_context`, `unclear_relationships`,
  `spoiler_inside_clip`, `dead_air`, `starts_mid_action`, `ends_mid_action`;
- provenance: evaluator, requested and reported model, provider, prompt version, attempts,
  whether the result came from the cache, and the cache key.

*Cross-episode ranking, the headline list and exports are deferred to a later phase.* The
planned diversity rules (at most 2 per episode, IoU ≤ 0.3) are unchanged.

## 9. Deterministic code vs. LLM responsibilities

| Deterministic code (trusted) | LLM (advisory, validated) |
|---|---|
| Parsing, timestamps, IDs, candidate windows | 0–5 judgements on the four criteria |
| Evidence windows (first 3 s, final 5 s), context ids | Which supplied ids create the hook and the cliffhanger |
| Validating every id by interval intersection | Short hook, cliffhanger, why and weakness explanations |
| Weighted score, cache, fallback labelling, usage stats | Quality-warning codes |

The model never sees or produces timestamps it could change. It never sees the baseline's
priority or anchor reasons, so there is no anchoring on the heuristic.

**Local validation** (`evaluator.validate_batch`) runs even when strict mode was requested. It
checks that:
- the JSON parses, apart from one tolerated ``` fence;
- the Pydantic schema holds: extra keys forbidden, scores 0–5, explanations 1–300 characters;
- every expected candidate appears exactly once (duplicates reject that candidate, unknown ids
  are ignored, missing ids are retried);
- hook ids exist, belong to the episode, and **intersect** `[start, start+3 s)`;
- cliffhanger ids intersect `[end−5 s, end)`;
- with empty hook or cliffhanger evidence, the matching score is ≤ 1;
- any event id mentioned in an explanation intersects the clip, so claims cannot point outside
  it.

## 10. LLM integration, privacy routing and failure handling (implemented in Phase 3)

*Phase 3.5: the project now runs on GroqCloud (§10b). This section still describes the
OpenRouter dialect, which is kept and tested.*

**Adapter** (`llm_client.py`). This is a small httpx client for OpenAI-compatible
`/chat/completions`. The defaults are `FRAMECUE_LLM_ENDPOINT=https://openrouter.ai/api/v1/chat/completions`
and `FRAMECUE_LLM_MODEL=google/gemini-3.1-flash-lite`. To swap models, change one environment
variable. Another OpenAI-compatible endpoint (such as a Kuku-provided API) only needs a
different endpoint and key. Evaluator code depends only on the `JsonModelClient` protocol.

**Every request sends:**
- `response_format={"type": "json_schema", "json_schema": {"strict": true, ...}}`. Candidate ids
  are an enum.
- `provider={"require_parameters": true, "data_collection": "deny", "zdr": true}`.
- temperature 0, `max_tokens`, and `stream=false`.
- No tools, plugins or web search.

`data_collection` cannot be configured to `allow`. If no endpoint satisfies these constraints,
the error becomes the run-level `no_eligible_provider`, and the constraints are **never
relaxed**. A preflight check (§10a) runs before any content is sent and may add
`provider.ignore`.

**Batching.** Candidates are evaluated per episode, in chronological order, in batches of
`FRAMECUE_EVAL_BATCH_SIZE` (default 8). For the real pack that is 100 requests for 788
candidates. Each batch sends:
- the episode logline and continuity notes;
- one de-duplicated event table covering every clip and its ±10 s context;
- per candidate: start, end, duration, clip ids, first-3 s ids, final-5 s ids, cut-at-boundary
  ids, and context-before and context-after ids.

The measured user payload is 6–27 k characters per batch (median 18 k).

**Prompt.** `prompts.py` holds `PROMPT_VERSION = "promo-eval-v1"`, which is part of every cache
key and every evaluation's provenance. The prompt says that screenplay strings are
**untrusted data**, never instructions. It also says that the before and after context is for
the evaluator's understanding only. The promo viewer sees just the clip, and ending at the
episode end is not a cliffhanger by itself.

**Recovery.**
- *Individually valid items* are kept.
- *Invalid, missing or duplicated items* are re-sent with ID-only validation notes, split in
  half each time.
- *A truncated response* (`finish_reason=length`) or malformed JSON fails the whole batch,
  which is then split.
- Each candidate is in at most `max_attempts=3` requests. After that it becomes
  `heuristic_fallback`, or `failed` if fallback is disabled, with a reason.

**HTTP errors.**
- 429 (honouring `Retry-After`), 5xx, 408 and timeouts get bounded exponential backoff, at most
  3 retries.
- 401, 402, unsupported model or parameters, and no eligible provider are **run-level**: all
  further requests stop, and every remaining candidate is labelled with the reason.
- Other errors fail only their batch, so one bad episode never stops the run.
- An optional `FRAMECUE_MAX_RUN_COST` stops requests once the provider-reported cost (or, if
  that is missing, the configured-price estimate) reaches the cap.

**Cache** (`runs/cache/evaluations/`, gitignored). One JSON file per candidate holds only the
validated assessment and its provenance, never prompts or raw responses. The key is the sha256
of:
- the episode file checksum;
- the candidate id and boundaries;
- the prompt version and scoring-rules version;
- the model, endpoint, temperature, max tokens and privacy settings;
- the evidence and context windows.

Rubric *weights* are deliberately left out of the key, because the weighted score is
recomputed on every read. Cached entries are re-validated against the episode on read.
`force_refresh` bypasses reads.

**Usage accounting.** The run records requests, HTTP retries, prompt and completion tokens,
**provider-reported** cost (summed only when reported), latency, cache hits and misses, and the
models and providers that served. An **estimated** cost appears only when
`FRAMECUE_PRICE_*_PER_MTOK` is configured, and it is labelled as an estimate.

**Secrets.** The key is a `SecretStr`. It goes only into the Authorization header and is never
logged, repr'd, or included in errors or cache keys.

**Known limitations.**
- Live behaviour is verified on GroqCloud only (§10b). The OpenRouter dialect has not been live-tested.
- The default model is blocked under strict ZDR (§10a).
- Semantic judgement quality is unmeasured until human review (Phase 5).
- Whole-episode context is not sent: only the logline, the notes and ±10 s around each clip.

## 10a. Strict-ZDR eligibility preflight (added in Phase 3.5)

**Why.** OpenRouter's ZDR docs define a separate tier, "Safety Retention". Endpoints in it may
keep prompts that provider safety classifiers flag (Google: up to 90 days, not used for
training), and the docs say they do not meet a strict ZDR requirement. Under `zdr=true`,
OpenRouter still routes to them when the account allows Safety Retention, which is the default
for self-serve accounts, and the request-level flag cannot override that. So `zdr=true` alone
does **not** guarantee strict ZDR.

**What runs** (`eligibility.py`, before any screenplay content is sent):
1. `GET /api/v1/endpoints/zdr`, OpenRouter's public list. No key and none of our data is sent.
2. Keep the configured model's endpoints that list both `response_format` and
   `structured_outputs`.
3. An endpoint is *strict* only when `safety_retention` is explicitly `false`. A missing flag
   counts as retaining.
4. Decision:
   - strict endpoints exist → eligible. Providers that serve only Safety Retention endpoints
     for this model go into `provider.ignore`. A response naming one of them aborts the run
     with `privacy_routing_violation`;
   - only Safety Retention endpoints → **blocked** (`only_safety_retention_endpoints`), unless
     `FRAMECUE_LLM_ALLOW_SAFETY_RETENTION=true` is set deliberately;
   - one provider serves both kinds → blocked (`cannot_exclude_safety_retention`);
   - list unreadable, no ZDR endpoint, no structured-output endpoint, or `zdr=false` → blocked.

`--live` always runs the check, and refuses with exit code 3 when blocked. `--check-eligibility`
runs it during a dry run.

**Measured on 2026-11-10** (live public metadata; the list had 1,107 ZDR-listed endpoints, and
56 of them were Safety Retention, all from Google):

| Model | Result | Structured-output ZDR endpoints | Lowest listed price (USD/Mtok in/out) |
|---|---|---|---|
| `google/gemini-3.1-flash-lite` (default) | **blocked**: all are Google Vertex Safety Retention | 0 strict, 5 retaining | n/a |
| `google/gemini-3.5-flash-lite` | blocked: same reason | 0 strict, 5 retaining | n/a |
| `anthropic/claude-haiku-5.5` | eligible, strict | 3 (Google Vertex) | 0.10 / 0.50 |
| `openai/gpt-6-luna` | eligible, strict | 3 (Azure) | 0.10 / 0.50 |
| `deepseek/deepseek-v4.1-flash-1017` | eligible, strict | 42 (many hosts, mostly fp8/fp4) | from 0.006 / 0.25 |

These are listed capabilities, not measured behaviour. Structured-output compliance, quality,
latency and real cost for any of these models are unverified until a live run. Whether
`data_collection="deny"` on its own excludes Safety Retention endpoints is not documented; the
preflight does not rely on it.

## 10b. GroqCloud Free Plan (Phase 3.5, the project default)

**Why.** The assignment must cost exactly $0. GroqCloud's Free Plan serves `openai/gpt-oss-120b`
with strict structured outputs, and needs no payment method. Select it with
`FRAMECUE_LLM_PROVIDER=groq`.

**Request dialect** (`llm_client.py`, `provider="groq"`):
- `max_completion_tokens` (not `max_tokens`);
- `response_format` `json_schema` with `strict: true`. Groq documents strict mode on
  gpt-oss-120b as constrained decoding; our schema, including integer bounds and enums, was
  accepted live;
- `reasoning_effort` (default `low`) and `include_reasoning: false`, so reasoning text is never
  returned;
- temperature 0 and `stream: false`.
- **Not sent:** the OpenRouter `provider` routing object, `service_tier`, tools, plugins or
  search.

**Privacy.** Retention is an organisation setting on Groq (Data Controls → Zero Data
Retention). The owner reports it is enabled. It cannot be verified through the API, so the
eligibility check records `groq_account_zdr` and relies on it. Groq's data page says it retains
no inference data by default except reliability/abuse logs, which ZDR turns off.

**Free-plan safety (never switch to paid inference):**
- `service_tier` is never sent, so requests use the default on-demand tier. A response
  reporting any other tier aborts the run (`paid_tier_used`).
- The `x-ratelimit-limit-*` headers measured on 2026-11-10 were 1,000 requests/day and
  8,000 tokens/minute for gpt-oss-120b. Higher limits mean the organisation's plan changed, and
  the run aborts (`paid_plan_suspected`).
- Groq's docs say upgrading requires the user to add a payment method; no automatic upgrade is
  described. A `blocked_api_access`/spend-limit 400 is run-level.
- The code cannot see billing state. That the organisation stays on the Free Plan is the
  owner's responsibility (no payment method).

**Quota handling.**
- **Tokens per minute.** Each request is paced from the `x-ratelimit-remaining-tokens`
  header. Tokens refill continuously at limit/60 per second. The client reserves the estimated
  prompt (characters / 3, measured about 3.1 chars/token) plus the full output allowance.
- **Retries.** TPM 429s are retried after `Retry-After`. A wait over 60 s is run-level
  (`rate_limit_wait_too_long`).
- **Daily quota.** A tokens/requests-per-day 429 is run-level (`daily_quota_exhausted`). So is
  zero remaining requests/day.
- **Daily ledger.** Tokens per day are not exposed in headers. A local ledger
  (`runs/quota/`, counts only) enforces `FRAMECUE_DAILY_TOKEN_BUDGET` (default 180,000 under
  the documented 200K TPD), and the evaluator stops with `token_budget_exhausted`.
- **Oversized requests.** A 413, or a request whose estimate exceeds the TPM limit, is
  batch-level, so the evaluator splits it.
- **No lost work.** Accepted assessments are cached individually. A stopped run loses nothing,
  and re-running the same command resumes.

**Prompt v2/v3 (token-compact).** With v1, a 4-candidate batch was about 7.8k prompt tokens
plus output. That exceeded 8K TPM (HTTP 413, recovered by splitting). v2 changes:
- event rows are arrays;
- times are in seconds;
- event ids are per-episode aliases (`e<source_index>`), mapped back to real ids before any
  validation, so evidence validation is unchanged;
- unknown aliases are rejected as nonexistent ids, and repair notes are shown in alias form.

Measured on the same 4 candidates: v1 needed 2 requests and 14.1k tokens; v2 needed 1 request
and 7.0k tokens. v3 adds three general rules found by the private review:
- explanations may describe only in-clip events, never before/after context or absent
  actions;
- an off-screen line counts from its start;
- cite the 1–3 decisive events, not the whole window.

**Measured live results (2026-11-10).** All numbers are from real Groq responses:

| Run | Candidates | Requests (attempts) | Prompt / completion tokens | Result |
|---|---|---|---|---|
| Smoke, v1, low | 4 (ep 1) | 3 (3); one 413 split | 11,993 / 2,156 | 4/4 evaluated |
| Smoke, v2, low | same 4 | 1 (1) | 5,525 / 1,516 | 4/4 evaluated |
| Sample, v2, low | 16 (ep 7, 23, 41, 50) | 5 (7); two TPM 429s retried; one candidate re-asked after failing validation | 19,667 / 6,316 | 16/16 evaluated |
| Compare, v3, low | 8 (5 episodes) | 5 (5) | 17,241 / 3,871 | 8/8 evaluated |
| Compare, v3, medium, batch 2 | same 8 | 9 (9); one HTTP 400 and one truncation, both recovered by split/retry | 22,323 / 12,236 | 8/8 evaluated |

- **Validation:** 0 fallbacks in every run. One sample candidate failed local validation once
  and was accepted on its repair attempt; the problem text was not persisted. 0 strict-schema
  JSON failures. Provider-reported model: `openai/gpt-oss-120b`. Tier: `on_demand`.
- **Latency:** 2.6–4.4 s average per successful request. Wall time is dominated by TPM
  pacing, about one full request per minute.
- **Unexplained 400:** the medium run's HTTP 400 message was not recorded at the time. Since
  then, `failure_details` (code, status, truncated provider message) is written to the
  gitignored `client_stats.json`.
- **Cost:** $0. Groq reports no cost; nothing billed is estimated. Groq's list price is shown
  for reference only and is not applied.

**Chosen defaults.**
- `reasoning_effort=low`, batch size 4, `max_completion_tokens=2500`.
- **Why low:** on the same 8 candidates, medium gave more accurate explanation prose (no
  fabricated details, against 2 small ones at low) and ranked the 8 almost the same (Spearman
  0.90, mean |Δ score| 6.5). But medium used about 1.6× the tokens per candidate, needed batch
  size 2, and had one truncation.
- **Medium's role:** a re-check for the final top-ranked shortlist, not for the full pass.

**Full-pack strategy at $0** (788 candidates):
- **Cost per candidate:** 1.6–1.8k tokens at low when batches of 4 fit (measured on the
  sample), and 2.4k when large batches must be split to fit 8K TPM (measured on pass 1). The
  pack therefore needs about 1.4–1.9M tokens, which is **about 8–11 days** of free quota at
  180k/day. Requests/day (1,000) do not bind.
- **Actual coverage so far:** 41 of 788 candidates (5.2%) in 12 episodes with the current
  profile: 37 from pass 1 plus 4 more from the v3 comparison set (verified in Phase 4, §15).
- **Pass 1:** `--per-episode 4`, the top 4 per episode from the diversity-ordered shortlist
  (200 candidates). Every episode gets AI coverage first.
- **Pass 2:** `--per-episode 8`, then `--per-episode 0` (all). The cache makes each pass
  incremental.
- **Daily run:**
  `python -m framecue.eval_driver --zip <pack> --per-episode <N> --allow-large --live`.
  It stops on the daily budget or a daily 429, and reports `coverage after run: X/Y`.
- **Not done:** mixing models or providers to go faster. That would make scores
  non-comparable.

**Known weaknesses (from the private review).**
- **Context leakage.** The model still sometimes phrases a cliffhanger using knowledge from
  the after-clip context ("her discovery unseen"). The score is defensible, because the cut
  withholds a discovery, but the prose is not viewer-facing. Removing after-context would trade
  away cliffhanger judgment; not changed.
- **Fabrication at low effort.** Low effort occasionally adds a small unsupported detail to
  prose (2 of 8 in the v3 comparison). Evidence ids are always valid; prose is not
  machine-checked.
- **Score compression.** Pacing is mostly 3–5.
- **Small samples.** The sample is small (36 live assessments of 20 distinct candidates across 5 episodes, before the full-pack pass). These are
  subjective editorial checks, not accuracy or conversion metrics.

## 11. Quality evaluation

Every reported number must come from an actual run. Planned measurements:
1. **Structural validity:** share of output promos that pass all deterministic checks. Target
   100%; anything less is a bug.
2. **Human review:** the author labels a stratified sample (for example 30 candidates across rank
   bands), blind to scores. Labels cover the same rubric plus "would post: yes/no". Report
   precision@10 and @20 for "would post", and rank correlation with model and baseline totals.
3. **Baseline vs. LLM:** compare the same metrics on the same labelled sample, and list the
   cases where they disagree.
4. **Stability:** run the LLM evaluator twice and report top-20 overlap.
5. **Failure catalogue:** concrete bad picks by category, such as needing prior context, a hook
   that comes too late, a resolution before the cut, speaker-alias confusion or a spoiler.
   Each example uses an ID and timestamps, not script text, in public material.

The sample is small and single-annotator, and the write-up will say so.

## 12. Privacy and reproducibility

- The screenplay pack is confidential:
  - Inputs live in `data/private/` or as archives at the repo root. Both are gitignored, along with
    `*.zip`, `*.pdf` and extracted `episode-*.txt` files.
  - Run outputs (`runs/`, `outputs/`) contain script excerpts and are gitignored. Raw outputs for
    the submission are shared through a private link, not a public repo.
  - Tests use **synthetic fixtures written from scratch** (`tests/fixtures/synthetic_*.txt`) that
    copy the format, never the content.
  - Logs record IDs, counts and hashes, not script text.
  - LLM calls send script text to the configured provider. The brief allows AI tools; the
    provider is named in the manifest, and the offline baseline needs no network.
- Secrets are read only from the environment or a local `.env` (gitignored). `.env.example`
  holds placeholders only.
- Reproducibility comes from:
  - deterministic ordering everywhere,
  - a manifest per run (input hashes, config, versions, git commit, model, prompt version, call
    stats),
  - the response cache,
  - temperature 0.

## 13. Phases and completion criteria

| Phase | Deliverable | Done when |
|---|---|---|
| 0 ✅ | Repo foundation, input audit, this plan | Package installs. Smoke test and Ruff pass. Private files are proven ignored. |
| 1 ✅ | Models and parser with a validation report | All 50 episodes parse with 0 errors. The warnings match section 4. Unit tests on synthetic fixtures cover every listed irregularity. Time conversion round-trips exactly. |
| 2 ✅ | Candidates, boundary validator, heuristic baseline | All 50 episodes give validated shortlists offline. 100% of candidates pass validation. Tests cover the constraints. *Scope change:* the CLI, ranking and exports move to Phase 3 (Phase 2 was scoped to candidate discovery). |
| 3 ◐ | Evidence-grounded LLM evaluator, OpenRouter client, cache | Done: schema-validated and cached evaluation with repair, splitting and labelled fallback, and mocked tests for every failure mode. **Not done: a live smoke test (no API key).** The CLI, ranking and exports move to Phase 4. |
| 3.5 ✅ | Live validation gate | Done: real inference on the GroqCloud Free Plan ($0) with a smoke test, a 16-candidate sample across 4 episodes, a low/medium comparison, private quality review, prompt v2/v3 and quota-aware resumable runs (§10b). The OpenRouter strict-ZDR preflight is kept (§10a). Full-pack AI coverage is partial and accumulates daily (see AI_LOG). |
| 4 ✅ | Pipeline, ranking views, exports, CLI, FastAPI backend | `framecue analyze` and the API process the full pack offline, merge valid cached AI evaluations, keep AI and baseline rankings separate, and export JSON/CSV with a manifest (§15). |
| 5 | React/TS/Vite/Tailwind/shadcn UI, quality analysis and submission | The UI loads a run and shows both views, coverage, evidence and timelines, with accept/reject. Plus a human-labelled sample and a ≤ 2-page write-up. |

## 14. Assumptions

- Promo timestamps refer to the screenplay's episode-relative timeline, which we assume matches
  the produced video.
- The screenplay contains no recap or cold open, so any stretch is eligible.
- Where the brief's "first 3 seconds" calls for judgement, the code enforces a minimum (an onset
  inside 3 s) and the model judges the strength.
- Spoilers count as a soft penalty, not a hard filter. Promos for paid microdramas often lean on
  reveals.

## 15. Phase 4: orchestration, ranking, exports, API

**Pipeline** (`pipeline.py`) runs parse → candidates → baseline → cached-AI lookup and
returns a `RunResult`.
- **Offline:** it never creates a model client.
- **Default AI profile:** the Groq free profile that produced the cache (`openai/gpt-oss-120b`,
  reasoning low, prompt v3).
- **When a cached evaluation is used:** only when its key matches exactly (episode file
  sha256, candidate window, prompt version, scoring rules, inference profile, evidence
  windows) **and** its evidence re-validates against the parsed episode.
- **Cache audit:** every other entry is counted, never used: older prompt versions, other
  profiles or changed inputs, entries from other episodes, and rejected tampered entries.
- **Real pack:** 73 entries scanned, 41 used, 24 older-prompt (v1/v2), 8 medium-reasoning,
  0 rejected.
- **Failures:** genuine failures from live runs (validation exhausted) are recorded in the
  cache's `failures/` folder and reported as `failed`. A budget stop is not a failure.

**Ranking** (`ranking.py`; rules version `ranking-v1`) has two views and never mixes the
scoring systems:

| View | Members | Order | Label |
|---|---|---|---|
| `ai` | valid current-profile AI evaluations only | weighted score ↓, cliffhanger ↓, hook ↓, standalone ↓, pacing ↓, episode, start, end, id | provisional while coverage < 100 % |
| `coverage` | every shortlisted candidate | heuristic priority ↓, episode, start, end, id | unverified suggestion |

- **Near-duplicates:** greedy in rank order. A clip in the same episode with IoU ≥ 0.5, or
  overlap ≥ 80 % of the shorter clip, becomes an unranked alternative of the earlier primary.
- **Ranks:** positions in the full view; filters keep them.
- **No forcing:** nothing is forced into the top, so an episode ending ranks on its own
  score.
- **AI tie-breakers:** they stay inside the AI rubric and never use the heuristic.
- **Coverage rows:** they never carry AI explanations or evidence; `ai_score_available`
  only flags them.

**Exports** (`exports.py`): `manifest.json` (coverage, cache audit, sha256 per file,
completeness notes), `episodes.json`, `candidates.jsonl`, `ai_evaluations.jsonl`
(validated, derived), `raw_model_outputs.jsonl` (unmodified, when captured), two ranking JSONs
and two editor CSVs.
- **Determinism:** everything except the manifest timestamp is deterministic. A test
  compares repeat runs byte for byte.
- **CSV safety:** cells starting with `= + - @` are prefixed with `'`.

**Raw model outputs.** The Phase 3.5 runs cached only validated assessments. Raw responses
were not kept, and this is stated in the manifest. The evaluator now appends every
unmodified response to `runs/raw/*.jsonl`. Exports include those whose episode checksum
matches the input.

**CLI** (`framecue`): `analyze` (offline), `serve` (uvicorn, localhost by default) and
`evaluate` (passes through to the live driver; dry run unless `--live`).

**Free-quota scheduling.** `eval_driver --schedule coverage-first --levels N`:
- **Level 1:** the best un-evaluated shortlist candidate from each episode without an AI
  assessment, in episode order.
- **Level 2 and up:** the same for episodes with fewer than N.
- **Properties:** deterministic and resumable through the cache.
- **Today's plan:** 38 picks, about 107k estimated tokens, which is one free day for
  50/50 episode coverage. The local daily ledger is our accounting by UTC day; Groq's reset
  time is not documented, so its daily 429 is authoritative.

**API** (`api.py`; contract in `docs/API.md`):
- **App and store:** an application factory and a run store. The store keeps runs in memory
  (LRU, 8) and as private copies under `runs/api/<run_id>/` (30 kept): the parse state and
  exports, never the raw upload.
- **Run ids:** 128-bit random. There is no listing endpoint.
- **Uploads:** one `.zip` or `.txt` files, 25 MB and 120 files max, parsed in memory.
- **ZIP safety:** the parser's guards plus a new 100 MB decompressed-size cap.
- **Export downloads:** allowlisted file names only.
- **Errors and CORS:** sanitized errors (no tracebacks, no echoed input), explicit local
  CORS origins, `Cache-Control: no-store`.
- **No inference:** no model call in any endpoint.

**Measured on the real pack:**
- **Timing:** CLI end-to-end in about 3–5 s; pipeline about 2.2 s, dominated by candidate
  generation; upload-to-summary in 2–6 s; promos and detail responses in about 20 ms.
- **Counts:** 50 episodes, 788 candidates, 317 coverage primaries (471 alternatives), and 28
  AI primaries (13 alternatives).

## 16. Deployment: Vercel Hobby, one project, stateless (Phase 4b)

**Decision.** Vercel only, with one project and one URL. A Vite frontend and a Python FastAPI
backend run as one Vercel Function. There is no separate host, no database and no paid
service. Vercel "Services" (beta) is not used.

**Verified against Vercel's docs (2026-11-10):**
- FastAPI is detected from a root `app.py` exposing `app`.
- Dependencies come from `pyproject.toml`.
- Python 3.12 is the default (3.13 and 3.14 are supported); we pin it with
  `.python-version`.
- Request and response bodies are capped at 4.5 MB (413 `FUNCTION_PAYLOAD_TOO_LARGE`).
- Hobby gives 300 s max duration (we set 60 s) and 2 GB / 1 vCPU.
- The Python bundle limit is 500 MB. The filesystem is read-only apart from 500 MB of `/tmp`.
- `.vercelignore` uses gitignore syntax for CLI uploads and does **not** exclude `.env` by
  default.
- FastAPI's `app.frontend()` (available in our FastAPI 0.162.2) promotes a built frontend to
  the CDN, with `fallback="index.html"` for navigation requests.
- Hobby is for non-commercial use only.

**Why no run store online.** Instances do not share disk, and only `/tmp` is writable, so a
stored run can disappear between requests. `POST /api/analyze` therefore returns the complete
`AnalysisDocument` (`document.py`) in one response, and the browser holds it. The stateful
`/api/runs*` routes remain for local `framecue serve` only.

**Document design.**
- **Normalized:** each event appears once, and each episode has an `event_range`. Candidates
  reference ids and times, never repeated text.
- **Separate AI section:** AI judgements live in their own map, and the views are ordered id
  lists.
- **Measured:** 1.77 MB without AI and 1.81 MB with 41 AI judgements. Projected about 2.5 MB
  at 788/788 AI coverage, which is below the 4.3 MB response cap. No lossy compaction is
  needed.

**Limits enforced in code (`serverless.py`):**
- body 4.4 MB (Content-Length guard) and file content 4.0 MB;
- 600 ZIP entries, 40 MB decompressed, 200 episodes;
- response 4.3 MB.

Each limit returns a 413 with guidance to use the local CLI.

**AI results online without deploying them.** `framecue export-ai` writes the private cache
as a bundle. Uploaded as `ai_bundle`, its entries go through the same key match and
evidence re-validation as the local cache (`MemoryCache`). `coverage.cache.source` records
`uploaded_bundle`.

**Privacy.**
- `.vercelignore` is an allowlist. The excluded set is checked by a test that runs git's
  ignore engine over a synthetic tree.
- No AI cache, outputs, screenplay or `.env` can be uploaded.
- No model call is possible from the deployment.
- Responses under `/api` are `no-store`.
- Unknown `/api/*` paths return JSON 404s, never the SPA.
- Logs hold only counts and exception types.

**Measured locally** (real pack; Python 3.11 in-process, and the real `app.py` under
uvicorn on Python 3.12.7):
- **Timing:** about 2.2–2.35 s per full-pack `/api/analyze`.
- **Memory:** process peak RSS about 88 MB, with a 27 MB Python heap peak.
- **Tests:** all 305 tests pass on both 3.11 and 3.12.

**Not verified:** a real Vercel build or preview deployment. Vercel CLI 65.4.0 runs, but it
is not logged in.

## 17. Phase 5: editorial workspace and Vercel verification

**Stack:**
- **Runtime:** React 19.1.10, TypeScript 6.0.3, Vite 8.3.5, Tailwind 4.3.3; Radix
  (`radix-ui` 1.8.1) under small shadcn-style components, `lucide-react`, `motion`
  (LazyMotion), `idb`, `@tanstack/react-virtual`.
- **Fonts:** Inter and Fraunces, self-hosted via fontsource. Replaced by Manrope in the FrameCue redesign (§19).
- **Version policy:** every version is pinned and was published at least two weeks before
  use.
- **No external services:** no analytics or third-party requests.

**Contract.**
- **Endpoints:** only `GET /api/health` and `POST /api/analyze`.
- **Types:** generated from the stateless app's OpenAPI (`npm run gen:types`).
  `frontend/src/api/openapi.json` is guarded by a Python test against drift.
- **Validation:** responses, imports and restored IndexedDB records go through
  `validateDocument`. It checks the structure plus cross-references: views and AI
  judgements must point at real candidates, and evidence at real events.

**State and persistence.**
- **State:** one reducer and context; no state framework.
- **IndexedDB `framecue` v1:**
  - `analysis/current` holds `{meta, doc}`;
  - `review/<input sha256:analyzed_at>` holds `{reviews, ui}`.
- **Write timing:** the document is saved once, in the background, after analysis or
  import. Decisions, notes and position are saved with a 300 ms debounce.
- **Failure handling:**
  - a missing API, a blocked open (4 s timeout), a quota error or a corrupt or old record
    produces a `StorageError`;
  - the analysis then stays in memory with "Not saved";
  - corrupt records are deleted.

**Honesty rules in the UI:**
- the two scoring systems are never in one list or column;
- full-show rows carry no AI text; a marker shows only that an AI review exists;
- the AI view explains an empty or partial state instead of estimating;
- the timeline is labelled screenplay timing, not video.

**Vercel.**
- **How it is served:** one project, one function (`app.py`, 9.35 MB). The frontend is
  served from the CDN via `app.frontend()` plus `[tool.vercel.fastapi.static] cdn = true`.
- **Why `cdn = true`:** without it, our top-level middleware made Vercel keep the static
  files inside the function. The first preview served assets from the function in `iad1`;
  after the change they come from the edge (`x-vercel-id` shows `bom1` only, cache `HIT`).
- **Routing:** deep links (`/review`, `/review/x/y`) return `index.html` from the CDN, and
  unknown `/api/*` paths return a JSON 404.
  **Correction (Phase 6):** this was wrong once static files moved to the CDN. Deep links
  returned the function's JSON 404; see §18.

**Measured.**
- **Tests:** 58 Vitest unit and integration tests; 4 Playwright end-to-end tests (Edge)
  against the real `app.py`; 2 private visual-QA runs on the real pack.
- **Bundle:** landing 254 KB (82 KB gzipped). The workspace chunk is 273 KB (88 KB) and
  is prefetched while an analysis runs.
- **Interaction**, local, real pack plus the AI bundle, with the machine under heavy
  background load (about 48% CPU idle):
  - candidate switch, click to paint: 94 ms average, 172 ms max;
  - view switch: 112 ms;
  - search across 788 candidates: 38 ms;
  - refresh restore of the 1.8 MB document: 1.3 s;
  - list DOM: 17 rows of 788.
- **Local upload to workspace:** 10.5–15.6 s. That is dominated by server candidate
  generation, which took 2.2 s idle earlier and 4–6 s under the same load; the client's
  own share after the response is about 0.9 s.
- **On Vercel:** the fictional sample takes 0.77 s. The full pack plus bundle takes
  6.4–7.0 s end to end from India (3.1 s of server compute), and the result matches local
  exactly.

**Incident: unintended production deployment.** The first `vercel deploy` on a new
project is always assigned to production, which I did not anticipate. It created a public
production address. It contained only the allowlisted code and the fictional sample,
holds no keys, never calls a model and stores nothing. Later deploys are previews, protected
by Vercel Authentication. **Resolved:** with the owner's approval, the production deployment
was removed (`vercel remove`), and only the protected previews remained until the
deliberate release in §18.

## 18. Phase 6: final QA and release

**Production deployment (public, deliberate).** https://framecue-app.vercel.app. It is built
with `vercel deploy --prod` from the same `.vercelignore` allowlist and ships only code and
the fictional sample, with no keys and no AI cache. It never calls a model.

**Bug found and fixed in release QA: deep links on Vercel.** With
`[tool.vercel.fastapi.static] cdn = true`, `frontend/dist` is served from the CDN and is not
in the Python function. `create_serverless_app` therefore skips `app.frontend()` and its
`index.html` fallback, so `/review` returned the function's JSON 404. That broke reloading
the workspace, which the IndexedDB restore relies on. The Phase 5 note that deep links worked
was wrong. Local tests passed because locally `app.py` does see `frontend/dist`.
- **Fix:** a `vercel.json` rewrite, `/((?!api/|api$).*)` to `/index.html`. Vercel checks the
  filesystem first, so assets and the sample are unaffected, and `/api/*` still reaches the
  function, including its JSON 404.
- **Verified:** on a protected preview with `vercel curl`, then on production anonymously.
- **Guard:** the e2e suite can now target any deployment (`E2E_BASE_URL`), and
  `e2e/release.spec.ts` checks deep-link reload, same-origin-only traffic, no cookies or
  localStorage, and export then re-import.

**Flaky test fixed.** `test_deterministic_apart_from_time` failed intermittently because the
test ZIP helper used `writestr(name)`, which stamps the current time on each entry, so two
calls on either side of a 2-second ZIP timestamp tick produced different input checksums.
Entries now carry a fixed timestamp. The product was deterministic throughout.

**Final AI quality review (no human labels).** The AI coding assistant reviewed the top 10
AI-view primaries against a six-point rubric:
- hook in the first 3 s;
- unresolved cut;
- standalone;
- grounded explanation;
- exact timestamps;
- redundancy.

The detailed review quotes the screenplay and stays private, in the reviewer bundle.

Aggregate results:

| Criterion | Pass | Partial | Fail |
|---|---|---|---|
| Hook in the first 3 s | 6 | 3 | 1 |
| Unresolved cut | 8 | 1 | 1 |
| Standalone | 5 | 5 | 0 |
| Grounded | 5 | 4 | 1 |
| Timestamps | 9 | 1 | 0 |
| Redundancy | 6 | 2 | 2 |

Limitations, with evidence:
1. **After-clip leakage:** 2 of 10 explanations credit the clip with a line that comes just
   after its end. Prompt v3 reduced this but did not remove it.
2. **Permissive redundancy:** two cuts sharing 24.8 s (IoU 0.42, 64% containment) both rank
   as primaries. Per-scene diversity in the top N is a follow-up.
3. **Generous scores on quiet scenes:** a resolved, tender episode ending scored 77, and
   scores are compressed (69–86 across the top 10).
4. **Small embellishments at low reasoning:** 3 instances of details not stated in the clip.
5. **Off-clip hooks:** 3 clips open on a reply to the line before the clip.

**AI coverage at release.** 41 of 788 candidates (5.2%) in 12 of 50 episodes. That day's
free-tier ledger was already at 182k tokens, over the 180k budget, so no further live run was
made. The coverage-first plan (38 episodes, about 107k tokens) remains a documented,
one-command next step.


## 19. FrameCue rebrand and visual redesign

**Scope.** This changed the frontend's presentation and product naming only. Not changed:
- parsing, candidate generation, AI evaluation, ranking, evidence validation and exports;
- the endpoints and the Groq integration;
- IndexedDB behaviour.

Backend edits are limited to user-visible copy: the CLI banner, the OpenAPI titles, and the
analysis note "not stored by FrameCue".

**Name and identifiers.** The product is FrameCue everywhere: the UI, the page metadata, the
docs, the Python package and the `framecue` command, the `FRAMECUE_*` settings, the IndexedDB
database (`framecue`), the schema ids (`framecue-analysis-v1`, `framecue-shortlist-v1`), the
GitHub repository and the Vercel project.

Exported filenames now start with `framecue-`.

**Design system** (`frontend/src/index.css`):
- **Colours:** canvas `#F5F7FA`, surface `#FFFFFF`, subtle `#EDF1F6`, line `#E2E8F0` /
  `#CBD5E1`, ink `#142238`, ink-soft `#33435C`, muted `#64748B`, and the accent `#345CE0` (hover
  `#2649BC`). Status colours: ok, no and later. "Later" is slate, with no amber.
- **Timeline lanes:** four slate steps.
- **Type:** Manrope (variable, self-hosted), weight 500 for body text.
- **Sizing:** radius 6/8/12 px; buttons 32/36/44 px; inputs 36 px; icons 16 px.
- **Elevation:** one shadow, for popovers and menus only. Cards are replaced by hairlines and
  spacing.

**Logo.** A split frame (the shot) with a playhead cue through it, on a 32-unit grid with
2.6-unit strokes:
- `BrandMark` comes in two tones: duo, with an accent playhead, and mono.
- `BrandIcon` is the accent tile, also used as `public/favicon.svg`.
- `Brand` is the lockup.
- There is also `apple-touch-icon.png`.

I checked the mark at 16, 20, 32 and 64 px, in single colour and reversed, before choosing
it. Three alternatives were rejected: corner brackets read as a generic scan icon, an offset
playhead looked unbalanced, and a box with a playhead read as a terminal.

**Layout changes:**
- **Landing:** a single centred column. Headline, one sentence, then an upload panel:
  - a dropzone;
  - a selected-files list with Clear;
  - the AI-bundle row;
  - Analyze as the primary action and the sample as the secondary.
  
  Below the panel sit two privacy notes and a compact three-step strip. "Open saved analysis"
  moved to the top navigation.
- **Workspace:**
  - a white top bar, with a coverage chip that carries a progress bar;
  - the list on the canvas, with an episode tag, timecodes, duration, a two-line preview, a
    restrained score, status icons and an accent bar on the selected row;
  - the inspector on white, in sections separated by hairlines rather than cards;
  - the review controls as a segmented group;
  - the timeline with In and Out markers and a compact legend beside its heading;
  - the excerpt with accent window markers and an active-line wash.

**Visual QA.** Playwright captured every state with Edge at 1440, 1366, 768 and 390 px. Real
defects were found and fixed:
- the AI-bundle row was cramped on mobile;
- body text looked too thin;
- the disabled primary button was a washed-out blue;
- privacy notes wrapped with orphans;
- the dialog focused its close button first;
- the export menu label wrapped;
- CLI commands broke mid-word;
- the coverage popover labels wrapped;
- the view switcher animated its background, showing the wrong segment for 150 ms.

There is no horizontal overflow at 768 or 390 px.

**Timeline redesign (follow-up).** The inspector timeline is now a screenplay map. The
millisecond positioning and overlap rows from `buildTimeline` are unchanged.
- **Clip band:** In and Out times, the clip duration, and tinted end-caps for the first 3 s
  and the last 5 s.
- **Event ribbons:** one row of rounded chips per category, with soft hue coding (dialogue
  navy, action teal, sound violet, camera slate). Blue is reserved for the clip and the AI
  evidence rings.
- **Context:** shaded outside the clip, with dashed scene-change lines from the model's scene
  data.
- **Selection:** the selected event's exact time is pinned on the axis.
- **Event card:** previous and next stepping. Left and right arrow keys move between events.

Layout fix: the detail panes are now `relative`. Screen-reader-only text inside them was
absolutely positioned against the page, which made the document 37 px taller than the
viewport. Clicking evidence chips or using the timeline then scrolled the whole page and
pushed the top bar out of view.

**AI coverage chip.** "AI reviewed N / M" is `coverage.ai_evaluated / total_candidates` from
the analysis document. N counts validated AI judgements: the ranked AI primaries plus their
alternative cuts. On the real pack that is 41 (28 primaries and 13 alternatives).
- N is 0 whenever no AI bundle is supplied, because the deployment never calls a model and
  ships no cache.
- With the local `runs/ai_bundle.json` it is 41 of 788: 73 entries scanned, 24 from an older
  prompt and 8 from another profile, both ignored, and 0 failing validation.

When no bundle was supplied, the popover now says so.

## 20. Final polish and release

**Interface.** Less text and fewer prominent numbers; no new features in the inspector.
- **Inspector order:** clip header (episode, rank, timecodes, duration, review actions), then
  one score line, the AI judgement when there is one, the screenplay map, the script, and a
  collapsed **Details** section.
- **Structural score:** shown as `0.84 / 1 Structural score` with a one-sentence reason built
  from the start and end anchors. The six components and the method id moved into Details.
- **AI score:** one line with the four rubric scores; the model, provider and prompt version
  moved into Details. Nothing heuristic is labelled as AI.
- **List rows:** one preview line and a 66 px row; structural scores to two decimals.
- **No AI reviews:** the AI tab, the "AI reviewed 0 / N" chip and the AI-only filters are
  hidden, and one line says the ranking is structural and AI review is not available. With
  reviews, the AI-reviewed view opens first and the full show stays one tab away.

**Saved analyses (IndexedDB schema v2).** The database keeps every analysis instead of one.
- **Stores:** `analysis` and `review` keyed by analysis key, `library` for listing metadata,
  and `app.last` for the analysis reopened after a refresh.
- **Migration:** opening the v1 database moves the single `current` record under its key,
  keeps its decisions and notes, and makes it the last opened. Tested with a real v1
  database in Vitest and in the browser pane.
- **Start page:** Recent analyses with open, inline rename and delete with confirmation.
- **Duplicate uploads:** a SHA-256 fingerprint of the file contents (plus any bundle) finds a
  saved analysis of the same files and offers to open it. The sample reopens silently.
- **Backup:** "Back up all" exports every analysis with decisions and notes
  (`framecue-backup-v1`); Import adds the ones not already saved.

**Icons.** The favicon reuses the logo paths and colours, cropped to the mark, with a light
variant for dark tab bars. PNG icons (180, 192, 512) and a 16/32/48 px ICO are rendered from
the same paths in Edge; `site.webmanifest` lists them.

**Address.** `framecue.vercel.app` belongs to another Vercel project, so the production
address is `framecue-app.vercel.app`. Saved analyses live in each address's own IndexedDB;
Back up all and Import move them between addresses or browsers.

**Commit attribution.** Earlier commits carried an automatic `Co-Authored-By` trailer, which is
why GitHub listed a second contributor. Authors and committers were always the repository
owner. Automatic trailers are now off for this project; AI assistance stays documented in
`docs/AI_LOG.md` and the solution document.
