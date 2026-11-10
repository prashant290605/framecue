# AI Assistance Log

This log records significant AI-assisted decisions, corrections and failures as they happen.
Entries are factual and contain no confidential script content.

Tool used so far: Claude Code (Claude Opus 5.5) for repository setup, the input audit and the
architecture draft.

## Phase 0: foundation and input audit (2026-11-10)

### Decisions
- **Integer milliseconds for all times.** The audit found that 70 of 7,654 timestamps use full
  centisecond precision rather than a 0.1 s grid. Integers avoid float drift in duration and
  overlap checks. Milliseconds rather than centiseconds leaves room for future input formats.
- **The model never emits timestamps.** It selects from event IDs we supply, and code maps those
  IDs to times. This removes a whole class of hallucinated-boundary errors.
- **Batch LLM calls per episode, not per candidate.** This keeps calls to about 50 per pack and
  gives the model the context it needs to judge standalone clarity.
- **No runtime dependencies in Phase 0.** Pydantic arrives in Phase 1 with the models.

### Corrections and failures (AI output that was wrong and was caught)
1. **Case-sensitive audit parser missed one episode's scenes.** The first audit pass matched
   scene metadata keys case-sensitively. As a result, one episode's two scenes came out with no
   heading or times, and 8 lines came out as "text without event". The audit's own anomaly
   counters caught it. The fix was case-insensitive matching, which is now a stated parser
   requirement in PLAN §4.
2. **Bogus "timestamps without fractions" count.** A quick regex check reported 7,654
   fraction-less timestamps. That was a regex backtracking artifact: `\d+:\d+` matched a prefix
   of `MM:SS.cc`. The check was discarded. A digit-width tally confirmed that all 7,654
   timestamps are `MM:SS.cc`.
3. **Apparent encoding corruption was a console artifact.** The Windows terminal showed
   replacement characters in scene headings. Byte-level inspection showed valid UTF-8 (U+2013 en
   dash). The real risk is Python's Windows default encoding (cp1252), so the parser must open
   files with `encoding="utf-8"`.
4. **"Camera gaps" overstated missing coverage.** Checking CAMERA blocks alone found 112 gaps of
   up to 25.5 s. Checking the union of all event types showed that ACTION blocks fill those
   gaps, leaving at most 2.0 s uncovered. The candidate generator therefore uses CAMERA and ACTION
   boundaries as cut points (PLAN §7).

## Phase 1: parser and validation (2026-11-10)

### Decisions
- **Two-pass parser.** A line-oriented pass collects raw records. A validation pass then checks
  timing relationships. Pydantic models are built only for episodes with no ERROR, so a
  returned `Episode` is always consistent. Episodes with errors are excluded and reported, never
  returned in part. When an episode number appears more than once, every copy is excluded,
  because there is no principled way to pick one.
- **Three severities.** ERROR covers malformed or impossible data. WARNING covers source
  anomalies that are kept exactly as written. INFO covers legitimate concurrency. Overlap
  between different event types (dialogue over a shot) is normal and not reported at all.
- **Annotations kept raw.** Speaker parentheticals are stored as an ordered tuple instead of
  being split into delivery and modifiers, as the Phase 0 draft proposed. Normalization stays
  out of the parser. PLAN §6 is updated.
- **Lines are split on `\r\n`, `\r` and `\n` only.** `str.splitlines()` also splits on
  characters such as U+2028 and form feeds, which could break up prose.
- **Pydantic validation messages are not passed through.** They can echo input values, which
  here means script text. Diagnostics give only field locations and error types.
- **ZIP safety.** Entries are read in memory and never extracted. The parser rejects:
  - absolute paths, drive letters and `..` traversal,
  - symlinks,
  - encrypted entries,
  - duplicate names,
  - entries above 5 MB (both the declared size and the bytes actually read are checked),
  - archives with more than 10,000 entries.

### Corrections and failures
1. **Wrong return type, caught by the first real run.** `_EpisodeParser.parse` returned only
   the `Episode`, but its callers unpacked `(episode, issues)`. Running against the real ZIP
   failed immediately with an unpacking error. Fix: `parse` now returns the pair. This bug
   would have been caught by any unit test, but the real-data run came first.
2. **The encrypted-entry test was initially vacuous.** It set `ZipInfo.flag_bits |= 0x1`, but
   Python's `zipfile` writer silently clears that bit. The archive was therefore not encrypted,
   and the test failed for the wrong reason (the parser read the dummy bytes). Fix: set the bit
   in the raw local and central headers, and assert that the flag is really present before
   testing the parser.
3. **Test expectations fixed before the first run.** Reviewing the drafted tests before
   running them turned up three mistakes:
   - an off-by-one source line number (13 instead of 14);
   - an assumption that overlap diagnostics come in source order, when they actually come in
     chronological order;
   - a Windows-only trap: `write_text` translates newlines, which changes file hashes and would
     have made "directory vs ZIP" comparisons fail for the wrong reason.

### Verified results (Phase 1)
- The full pack parses with 0 errors. The counts match the Phase 0 audit exactly:
  - 50 episodes, 100 scenes, 3,727 events;
  - CAMERA 1,754, DIALOGUE 1,487, ACTION 372, SOUND FX 114.
- Diagnostics:
  - 8 source-order inversions (WARNING);
  - 1 SOUND FX ending 300 ms past its scene (WARNING);
  - 20 same-type overlaps (INFO): DIALOGUE 10 and CAMERA 5, both matching the audit, plus
    ACTION 4 and SOUND FX 1, which Phase 0 had not measured.

## Phase 2: candidate discovery and heuristic baseline (2026-11-10)

### Decisions
- **Structure creates windows; keywords only reorder them.** Anchors come from event onsets and
  ends, scene and episode boundaries, and "just before a line". A small set of generic cue
  stems, plus punctuation and delivery notes, affects only the heuristic priority. A synthetic
  test checks that an episode with no dramatic vocabulary still gets a full, region-covering
  shortlist.
- **Dialogue is protected by construction.** Any anchor that falls strictly inside a line is
  dropped. Cutting through camera, action or sound blocks is allowed but measured.
- **The pool is bounded and inspectable.** The 65,362 raw windows are counted, never built.
  For each start, the pool keeps the best ends per duration band (18,726 windows), and
  `generate_pool` exposes the pool for recall studies.
- **Readable candidate ids** (`C001-0046800-0081400`), not hashes. They are deterministic,
  unique per window, and easy to discuss in an interview. PLAN §6 records the deviation.
- **The CLI, ranking and exports move to Phase 3,** because this phase was scoped to candidate
  discovery. PLAN §13 is updated.

### Corrections driven by the real pack
1. **Alternative cuts crowded out distinct moments.** On the first full run, 444 of the 716
   shortlisted windows were alternative cuts of already-selected moments, leaving about 5.4
   distinct moments per episode. The episode ending was shortlisted in only 48 of 50 episodes.
   Fixes:
   - an explicit "best episode ending" seed;
   - a distinct-moments-only pass that reserves 4 slots for alternatives;
   - a final fill pass for short episodes.

   Result: 436 distinct moments (best per region, distinct moments and episode-ending seeds),
   the episode ending in 50 of 50 episodes, and the smallest shortlist at 9 (a 70 s episode).
2. **Many clips ended mid-shot.** A private inspection of sample boundaries, plus the
   measurements, showed that only 449 of 788 shortlisted windows ended on a camera cut. 368
   camera shots were sliced by a boundary. Clips tended to end the instant a line finished,
   while the shot ran on for another second. Fix: a `shot_end_after_line` end anchor at a
   camera cut up to 1.5 s after the latest line. Result: 705 of 788 end on a clean cut, and
   sliced camera shots fell from 368 to 126.

### Mistakes in my own work (caught by tests)
- Two drafted tests had wrong hand-derived expectations:
  - one used a 12.999 s timestamp, which `MM:SS.cc` cannot represent, and the parser rightly
    rejected it;
  - one expected a 14 s end anchor, but no end can fall below the 20 s minimum.

  Both tests were corrected, not the code.
- Another drafted test expected "a different line between the line end and the cut" to
  suppress `shot_end_after_line`. Working through the failure showed that this branch could
  never fire. Such a line either ends first, and so becomes the "latest line", or it straddles
  the cut, which makes the anchor unsafe and drops it. The dead condition was removed, the
  test now covers the real limit (the 1.5 s gap), and the full-pack output stayed identical.
- **Mutation check.** I temporarily turned the half-open intersection into a closed one and
  disabled dialogue protection. Each mutant made the tests fail (3 tests and 1 test
  respectively), so those tests are not just restating the implementation.

## Phase 3: evidence-grounded LLM evaluation (2026-11-10)

### Decisions
- **Architecture change: no Streamlit.** The product will be a React + TypeScript + Vite +
  Tailwind + shadcn/ui frontend over a thin FastAPI backend that calls this core package. Both
  arrive in Phase 4. PLAN §0 and §13 are updated.
- **0–5 integer rubric with behavioural anchors; Python computes the 0–100 weighted score.**
  This replaces the 0–10 draft, because a smaller scale with explicit anchors is easier to apply
  consistently. Empty evidence caps the related score at 1, and this is enforced locally.
- **The model sees ids, not adjustable timestamps,** and never sees the baseline priority. Every
  cited id is checked by interval intersection against the parsed episode.
- **A de-duplicated event table per batch.** Overlapping candidates share event rows instead of
  repeating screenplay text. The context sent is the logline, the continuity notes, and ±10 s
  around each clip.
- **Partial batches.** Each valid item is accepted on its own. Only invalid, missing or
  duplicated candidates are retried, split in half each time, with ID-only repair notes, for at
  most 3 requests per candidate. After that the candidate becomes a labelled heuristic fallback
  with no AI scores.
- **Run-level errors stop all requests.** These are 401, 402, unsupported model or parameters,
  and no privacy-eligible provider, and the constraints are never relaxed. Other errors fail
  only their batch.
- **Rubric weights are excluded from the cache key** (a deliberate deviation from the brief's
  key list). The weighted score is recomputed from cached 0–5 judgements on every read, so
  changing the weights can neither serve a stale score nor force a paid re-run.
- **Docs consulted.** I checked the OpenRouter docs (structured outputs, provider routing,
  errors, usage accounting, ZDR) and the public model metadata. `google/gemini-3.1-flash-lite`
  exists, and all 8 of its endpoints list `response_format` and `structured_outputs`. The
  metadata I could read did not show a ZDR endpoint for this model, so the default `zdr=true`
  may fail with `no_eligible_provider` on a live run. That is the intended safe behaviour, but
  it is unverified.

### Mistakes caught during this phase
- A drafted private test expected 104 prompt batches. The correct count is 100, because each
  episode's 9–16 candidates make exactly two batches of up to 8. The test was fixed.
- A prompt-injection test compared dialogue text without its surrounding quotes. The parser
  keeps the quotes by design (Phase 1), so the test expectation was fixed.
- I appended to `schemas.py` with a bash heredoc. The heredoc failed to parse because of a
  quote in a description string, and nothing was written. I switched to the editor tool.
- **Mutation check.** I disabled each of these in turn:
  - interval-intersection validation,
  - the empty-evidence score cap,
  - duplicate detection,
  - cache re-validation,
  - batch splitting.

  Each mutant made at least one evaluator test fail (3, 1, 1, 1 and 3 failures respectively).

### Not verified
- **No live API call was made:** no API key was configured in this environment. Model quality,
  real latency, real cost and real structured-output compliance are all unmeasured. The only
  cost figure is a labelled ESTIMATE for the full pack at the published standard prices:
  about 549 k prompt and 173 k completion tokens, about $0.40.

## Phase 3.5: live validation gate (2026-11-10)

### Configuration check
- No `.env` file exists. Neither `PROMOLENS_LLM_API_KEY` nor `OPENROUTER_API_KEY` is set in the
  process, user or machine environment. `LLMSettings.from_env()` loads no key. **Live inference
  is blocked**, and no request was sent to the model API.

### Provider eligibility (verified against live public metadata)
- Read `GET /api/v1/endpoints/zdr` (public, no key) and the model's endpoint list.
  `google/gemini-3.1-flash-lite` exists, and all 8 endpoints support `response_format` and
  `structured_outputs`. 5 of them (all Google Vertex) are on the ZDR list.
- **Correction to Phase 3.** Phase 3 said ZDR availability was "unconfirmed" and that a live
  run "may fail with `no_eligible_provider`". Both were wrong:
  - all 5 ZDR-listed endpoints carry `safety_retention: true`;
  - OpenRouter's ZDR docs say Safety Retention endpoints do not meet strict ZDR, and are still
    used under `zdr=true` when the account allows Safety Retention (the self-serve default);
  - so the Phase 3 request would most likely have **succeeded** and been routed to an endpoint
    that may retain flagged prompts. That is a silent privacy weakening, the opposite of the
    safe failure documented.
- **Fix.** Added `eligibility.py`, a preflight that runs before any content is sent:
  - it accepts only endpoints whose `safety_retention` is explicitly false, and treats a
    missing flag as retaining;
  - it adds Safety-Retention-only providers to `provider.ignore`;
  - it aborts the run if a response names an excluded provider;
  - it blocks the default model unless `PROMOLENS_LLM_ALLOW_SAFETY_RETENTION=true` is set
    deliberately.

  `--live` always runs it. Verified against the live list, it blocks the default model and
  passes `anthropic/claude-haiku-5.5`, `openai/gpt-6-luna` and
  `deepseek/deepseek-v4.1-flash-1017` (PLAN §10a).
- **Not changed:** the default model. Choosing between switching to a strict-ZDR model and
  accepting Google Safety Retention is a privacy decision for the project owner.

### Tests
- 11 new mocked tests (`tests/test_eligibility.py`). They use synthetic ZDR rows and no
  network.
- Mutation check: treating every endpoint as strict (3 failures), removing the
  forbidden-provider check (1), and treating a missing flag as strict (1) each make the tests
  fail.

### Not done (blocked)
- Live smoke test, the 12+ candidate representative sample, the private quality review, and
  real cost, latency and token measurements. None of these was run, and no model quality claim
  is made.

## Phase 3.5 (continued): GroqCloud Free Plan and real inference (2026-11-10)

### Provider change
- **Why:** the owner switched the project to the GroqCloud Free Plan so that the assignment
  costs exactly $0. Model: `openai/gpt-oss-120b`. The owner enabled organisation-wide Zero Data
  Retention in the Groq console. The API cannot verify that, so it is recorded as relied upon.
- **Adapter:** `llm_client.py` gained a `groq` dialect. The OpenRouter dialect is unchanged
  and still tested. The Groq dialect sends:
  - `max_completion_tokens`;
  - strict `json_schema`;
  - `reasoning_effort`;
  - `include_reasoning=false`.

  It does not send the OpenRouter `provider` object, `service_tier`, tools or plugins.
- **Config check:** `.env` holds `PROMOLENS_LLM_PROVIDER=groq`, a key with the expected shape
  (length and prefix checked only, never printed), the model and the endpoint.
  `LLMSettings.from_env()` loads the key, and it does not appear in the settings repr.
- **Docs consulted:** Groq rate limits, structured outputs, the model page, errors, reasoning,
  data retention, billing FAQs and spend limits. Groq's rate-limit page labels its table as
  Developer-plan base limits, so the free-plan limits were taken from the live
  `x-ratelimit-*` headers instead: 1,000 requests/day and 8,000 tokens/minute. Tokens/day is
  not in any header. 200K is the documented figure, and it is not confirmed for the Free Plan.

### Probe before sending any screenplay content
- A synthetic request using the real strict schema returned HTTP 200 with schema-valid JSON.
  It reported `service_tier=on_demand` and `reasoning_tokens` counted inside
  `completion_tokens`. With `include_reasoning=false`, no reasoning text came back.
- Low vs medium on that tiny probe: 106 vs 704 reasoning tokens.

### Problems found live, and fixes
1. **Request too large for the free TPM limit.**
   - **Symptom:** the first live smoke batch (4 candidates, prompt v1) got HTTP 413. The
     existing split recovery evaluated it as 2 + 2, but each half used about 6k prompt tokens.
   - **Cause:** v1's JSON event objects, 16-character event ids and `MM:SS.cc` times tokenise
     at about 2.6 chars/token. 59 events took 10.9k characters, of which only 3.7k were
     screenplay text.
   - **Fix:** prompt v2 uses array rows, times in seconds, and per-episode aliases (`e<n>`)
     that are mapped back to real ids *before* validation. Unknown aliases are still rejected.
   - **Result:** the same 4 candidates now take 1 request and 7.0k tokens, down from 2 requests
     and 14.1k.
2. **TPM 429s despite pacing.** The pacing estimate reserved half the output allowance at 3.5
   chars/token; measured use was about 3.1 chars/token plus the output. The estimate now
   reserves the full allowance at 3.0 chars/token. The following runs had 0 rate-limit 429s.
3. **Quality problems found in the private review** (details kept in the gitignored
   `runs/review/`):
   - **Fabricated details:** 3 of 16 v2 explanations described details that no clip event
     contains.
   - **Context leakage:** one explanation leaked after-clip knowledge.
   - **Missed hook:** one hook was missed because the line was off-screen dialogue over
     another shot.
   - **Over-citation:** cliffhanger evidence often listed the whole final-5-second window.

   The fix is prompt v3, which adds three general rules (PLAN §10b). It was not tuned to any
   one episode. On 8 comparison candidates across 5 episodes:
   - the missed hook and the fabrications were fixed;
   - citations shrank to 1–3 decisive ids;
   - the after-context leak persists in 1 of 8.
4. **Low vs medium reasoning** on the same 8 candidates:
   - **Prose:** medium had 0 fabricated details, against 2 at low.
   - **Ranking:** almost the same (Spearman 0.90, mean |Δ| 6.5).
   - **Cost:** medium used 4.3k tokens per candidate against 2.6k (both at small batch fill),
     needed batch size 2, and had one truncation and one HTTP 400, both recovered.

   Decision: low is the default, and medium is a re-check for the final top picks. The 400's
   message was not recorded at the time. `failure_details` and a `validation_log` (ids and
   codes only) now go to the gitignored run files.

### Verified live results (real Groq inference, $0)
- **Volume:** 5 live runs on real content before the full-pack pass, plus 2 synthetic probes.
  They accepted 36 candidate evaluations (4 + 4 + 16 + 8 + 8), covering 20 distinct
  candidates across 5 episodes, with 0 heuristic fallbacks. One sample candidate failed local
  validation once and was accepted on its repair attempt.
- **Tokens:** exact per-run counts are in PLAN §10b. Latency was 2.6–4.4 s per successful
  request. Groq reports no cost, and none is invented.
- **Baseline vs AI:** across the 20 v2-low assessments, Spearman(heuristic priority, AI
  weighted score) = 0.60. The AI correctly scored a baseline top-3 clip's resolved, fade-out
  ending as cliffhanger 0. It also lifted a low-ranked scene-crossing clip, though arguably
  with generous pacing.
- These are subjective spot checks on a small sample. No accuracy or conversion metric is
  claimed.

### Full-pack, zero-cost strategy and actual coverage
- **Strategy:** PLAN §10b. Run passes of `--per-episode 4`, then `8`, then `0`, once per day
  under a 180k-token budget. The cache makes every run resumable.
- **Pass 1, day 1 (measured):** 200 candidates selected (top 4 per episode); 4 were already
  cached from the v3 comparison.
  - **API:** 15 HTTP calls, 0 failures, 0 rate-limit 429s.
  - **Stopped by the budget:** the run stopped itself with `token_budget_exhausted`, and the
    remaining 163 candidates were labelled `heuristic_fallback`. They are not cached and are
    retried on resume.
  - **Tokens:** 64,583 prompt and 14,694 completion for 33 new assessments, about 2.4k per
    candidate.
  - **Ledger overshoot:** the day's ledger ended at 182,121 tokens, 2.1k over the 180k budget,
    because the check runs before each request. That is still under the documented 200K.
- **Actual coverage after day 1:** 37 of 200 pass-1 candidates (18.5%). That is **37 of 788
  candidates in the full pack (4.7%)**, from 10 episodes (1–9 and 41). Full-pack AI evaluation is
  **not complete**.
- **Inefficiency found and fixed:** 24 of the 33 new assessments used 2 attempts, because
  batches whose estimate exceeded 8K TPM were split locally before sending, and each split
  consumed one of the 3 model attempts.
  - **Fix:** a size split (`request_too_large`) no longer counts as an attempt. Regression
    test added, and a mutant without the refund fails it.
  - **Visibility:** local rejections are now recorded as `request_too_large_local` in client
    stats.
- **Revised projection:** about 1.7–2.4k tokens per candidate, so about 1.4–1.9M tokens for
  the pack. At 180k/day that is **about 8–11 days** at $0, if the Free Plan TPD is 200K as
  documented.
- **Spot check:** 4 pass-1 assessments from episodes not in the sample (2 and 5) were sound.
  There was one minor embellishment ("reaches for" where the action is only spoken).

## Phase 4: pipeline, ranking views, exports, CLI and API (2026-11-10)

### Decisions
- **Two ranking views, never one combined list.** AI scores (0–100) and baseline priorities
  (0–1) are different systems.
  - The `ai` view holds only validated AI evaluations and is provisional until coverage is
    complete.
  - The `coverage` view holds all 788 candidates, labelled unverified.
  - Coverage rows never carry AI text even when a review exists; a flag points to it instead.
- **Near-duplicate rule.** IoU ≥ 0.5, or ≥ 80 % containment of the shorter clip, within an
  episode makes a clip an unranked alternative cut.
  - **Why containment was added:** the shortlist's IoU-only diversity let a 20 s clip nested
    inside a 59 s clip count as distinct.
  - **Result:** 317 coverage primaries out of 788.
- **AI tie-breakers stay inside the rubric.** Cliffhanger, hook, standalone and pacing break
  ties, then position. The heuristic is never used, so the AI ordering does not depend on the
  baseline.
- **The cache profile is explicit.** `analyze` defaults to the Groq free profile that
  produced the cache, not to whatever `.env` holds. A reviewer's machine without `.env` gets
  the same result.
- **No raw-output reconstruction.** The cache held only validated assessments, so there are
  no raw responses for the 41 evaluations, and the manifest says so. Raw capture now runs for
  future live calls.
- **Run store.** A run store with memory and private files replaces a database or queue; the
  full pack analyses in about 2 s.

### Verified
- **Counts:** the real pack through the CLI gave 50 episodes, 788 candidates and 41 valid AI
  evaluations in 12 episodes. The handoff had said "37 of 788", which counted only pass 1's
  top-4 selection; the v3 comparison set adds 4 more current-profile entries.
- **Cache audit:** 73 entries scanned, 41 used, 24 excluded as older prompt versions, 8
  excluded as another profile (medium reasoning), 0 rejected.
- **Independent export check** (a separate script using only json/csv and its own maths):
  - all weighted scores recompute from the raw rubric values;
  - every AI evidence id exists and lies in its window, checked against event times
    re-parsed from the ZIP;
  - timestamps round-trip;
  - both views are correctly ordered, and every alternative scores ≤ its primary;
  - coverage rows carry no AI fields;
  - the CSV order matches the JSON;
  - every episode has a coverage primary.

  0 problems found.
- **Live server:** `promolens serve` on localhost served health, CORS (allowed origin only),
  a full-pack upload, promos and detail in about 20 ms, CSV download and the OpenAPI docs.
  The server log held no screenplay text and no tracebacks.
- **Model calls:** 0 live model calls in Phase 4. The evaluate path was only dry-run.

### Mistakes caught
- `Query(ge=1)` on a list parameter made FastAPI compare a list with an int (500). The error
  guard returned a sanitized 500 as designed. The fix validates manually, and a test covers
  `episode=0`.
- `promolens evaluate --zip ...` failed because argparse claimed `--zip` for the top-level
  parser. Arguments are now passed straight through, with a regression test.
- Two drafted tests were wrong:
  - a tie-breaker check was guarded by an `if`, so it could pass vacuously; it is now a hard
    assertion;
  - an API test looked up an AI pick among coverage *primaries*, but it was an alternative
    cut there.

## Phase 4b: Vercel deployment adaptation (2026-11-10)

### Decisions
- **Single Vercel project.** A root `app.py` builds the stateless app, and FastAPI's
  `app.frontend()` serves the Vite build. I checked that this API exists in our installed
  FastAPI 0.162.2 before relying on it. Vercel Services (beta) and `builds` are not used.
- **Stateless `/api/analyze`** returns one normalized `AnalysisDocument`; there is no run
  store online. I measured it before deciding whether to compact: 1.81 MB against a 4.5 MB
  limit, so readable field names stay.
- **AI bundle upload** instead of deploying the cache. The deployment ships no confidential
  data, and AI results appear online only when the user supplies their own bundle. Server-side
  validation matches the uploaded screenplay, so a bundle cannot attach scores to a different
  input.
- **`.vercelignore` as an allowlist.** Vercel does not read `.gitignore` and does not ignore
  `.env` by default, so a denylist was too fragile.

### Mistakes caught
- With the frontend mounted, an unknown `/api/...` GET fell through to the SPA's
  `index.html` (200 HTML). A test caught it. Fix: an explicit `/api/{path}` catch-all that
  returns a JSON 404, hidden from OpenAPI.
- My first memory measurement ran under `tracemalloc` and reported 13 s per request. The
  tracer slows Python several-fold. Re-measured without it: about 2.3 s.
- The Windows memory probe returned 0 until the ctypes handle types were set.
- Building the Python 3.12 venv made setuptools leave a `build/` folder in the repo. It was
  removed.
- Two test bugs:
  - Windows text-mode stdin sent `\r\n` to `git check-ignore`;
  - an httpx multipart request must be `.read()` before its length is known.

### Verified
- **Full suite:** 305 tests pass on Python 3.11.8 and on 3.12.7 (a uv-created venv).
- **Real entrypoint:** `app.py` under uvicorn on 3.12 served the page, a deep link and an
  asset; returned a JSON 404 for an unknown API path; analyzed the full pack with a bundle
  (200, 1.81 MB, 2.35 s, 41 AI); and rejected a 4.5 MB body with a 413 that names the CLI.
- **Parity:** the AI order, coverage order, candidate times, ranks and AI scores match the
  local CLI exports exactly.
- **Live AI:** 0 live model calls.

### Not verified
- A Vercel build or preview deployment. The CLI is installed via `npx vercel@65.4.0` but is
  logged out. `vercel dev`, `vercel build` and `deploy` need the owner's login.
  `vercel deploy --temporary` (no login) was deliberately not used, because it would publish
  an unclaimed public deployment.

## Phase 5: editorial workspace, persistence and Vercel verification (2026-11-10)

### Decisions
- **A fictional "Try sample".** The private pack can never be deployed, so I wrote an
  original three-episode story, *The Harbor Light*, with a generator that emits the parser's
  exact format. It parses with 0 issues and gives 44 candidates.
  - **Why I extended it:** the first version, with 50 s episodes, gave only 7 distinct
    moments, because 20–60 s clips overlap heavily in short episodes. A third scene per
    episode raised that to 10.
- **Test fixtures without a live model.** They are produced by the real API on the
  fictional sample. The AI fixture's judgements come from the real evaluator with a mocked
  model (canned text), labelled as such.
- **Small state model.** One reducer, IndexedDB via `idb`, and decisions keyed to the
  analysis identity, so decisions never attach to a different document.
- **Bundle split.** The workspace chunk is lazy and Motion and Radix providers moved into it.
  The landing page went from 578 KB to 254 KB, measured from source maps rather than
  guessed.

### Mistakes caught
- **Detail lagged behind the selection.** `AnimatePresence mode="wait"` kept the previous
  candidate on screen until its exit animation finished. A decision clicked in that window
  could apply to the wrong candidate. Tests caught it (jsdom never finishes the exit). The
  detail now switches immediately, with only a short fade-in.
- **jsdom test environment.** The virtualizer measures `clientWidth/Height`, which jsdom
  reports as 0, so no rows rendered. The test setup now gives elements a size.
- **Visual QA findings, fixed:**
  - the end-time label wrapped when a clip ended at the episode end;
  - adjacent camera shots merged into one bar;
  - a semi-transparent sticky excerpt header showed text through it;
  - tick labels crowded on phones;
  - sizes showed "3.81 MB" (binary units) against the documented 4.0 MB.
- **`vercel link` damaged `.gitignore`.** It appended `.env*` after our `!.env.example`
  exception, which silently ignored the placeholder file. I restored the exception; `.env`,
  `.env.local` (Vercel's OIDC token) and `.vercel/` stay ignored.
- **Static assets ran through the function.** The first preview served them from Python,
  because Vercel keeps static files in the function when top-level middleware exists (the
  FastAPI docs confirm this). The fix is `[tool.vercel.fastapi.static] cdn = true`, which is
  safe because our middleware only affects `/api`. Verified from edge headers.
- **Unintended production deployment.** The first deploy of a new project is assigned to
  production, which the user had not asked for. It contains only public code and the
  fictional sample; details are in PLAN §17. I reported it to the user, and removed it only
  after they approved. The public URL now returns 404; the two protected previews remain.
- **Misleading slow-upload number.** Local upload-to-workspace measured 10.5–15.6 s. Timing
  showed the time was server candidate generation, and the same in-process pipeline ran
  2–2.5× slower than an hour earlier under background CPU load. I report both rather than
  the best number.

### Verified
- **Python:** 307 tests pass with the private ZIP (283 without); Ruff is clean.
- **Frontend:** 58 Vitest tests and 4 Playwright end-to-end tests in Edge (upload, refresh
  restore, export, deletion, size limit, deep links, mobile). The 2 private visual-QA runs
  wrote screenshots to the gitignored `runs/screens/`.
- **Vercel preview** (protected; checked in the browser pane after the owner signed in):
  - the landing page renders;
  - assets, the sample and the favicon come from the CDN edge;
  - `/api/health` and the JSON 404 work, and deep links serve `index.html`;
  - the sample analyzes through the function;
  - a decision survives a reload (IndexedDB), with no localStorage or readable cookies.
- **Full pack on Vercel:** 200, 1.81 MB, matching the local engine exactly. This was sent
  via curl to the public deployment with the identical backend.
- **Model calls:** 0 live model calls.


## Phase 6: final QA, release and submission (2026-11-10)

**What the AI assistant did:**
- ran the privacy audit before the first commit;
- regenerated and independently validated the full-pack exports;
- did a rubric review of the top 10 AI picks, labelled as an AI-assistant review and not human
  labels;
- made the production deployment and verified it anonymously with curl and Playwright;
- drafted the solution PDF and the walkthrough script, which stay private until the owner
  submits them.

### Mistakes caught and corrected
- **Wrong deep-link claim (Phase 5).** I had recorded that deep links served `index.html`
  on Vercel. After `cdn = true` moved static files out of the function, `/review` returned a
  JSON 404, on the old preview and on the new production deployment alike. Release QA caught
  it with anonymous curl. Fixed with a `vercel.json` rewrite, verified on a preview and then
  on production, and now covered by `e2e/release.spec.ts` running against the deployment.
- **Flaky determinism test.** `test_deterministic_apart_from_time` failed 1 run in 3. A
  diffing script showed only `summary.input.sha256` differed: the test helper stamped the
  current time on each ZIP entry. The helper now uses a fixed timestamp, and 4 consecutive
  runs pass.
- **Rank numbering in the private review.** I first numbered ranks 9 and 10 by episode
  intuition. Checking `rankings_ai.json` showed the tie-break order (episode 7 before
  episode 9), and I corrected the review.

### Verified
- Live production: https://promolens-two.vercel.app, all checked anonymously:
  - health, assets and the sample are served from the CDN;
  - deep links return 200 HTML, and unknown `/api` paths return a JSON 404;
  - the Playwright suite (5 tests) passes against production;
  - every request is same-origin (no model or third-party calls);
  - no cookies and no localStorage;
  - the served bundles contain no confidential names and no key or provider strings.
- AI coverage is unchanged at 41 of 788 (5.2%). No live model calls were made in Phase 6
  (the daily ledger was over budget).

## FrameCue rebrand and redesign (2026-11-11)

**What the AI assistant did:**
- rebuilt the design tokens and drew the FrameCue logo, choosing it from rendered comparisons
  at 16–64 px;
- restyled every frontend surface without changing any calculation;
- updated the copy and docs;
- ran screenshot QA at four widths, then deployed after a preview check (§19).

### Mistakes caught and corrected
- **A truncated component.** A scripted splice cut the upload screen at the first match of
  its marker, which was inside a helper, and dropped most of the component. The blank page
  and console error caught it. I restored the file from git and spliced at the right place.
- **An unrelated process on port 8000.** The dev proxy now reads `API_TARGET`, and the local
  run uses port 8010. I did not stop the other process.

### Tests updated because the visible copy changed (none removed)
- The landing-page heading is now the h1, "Find the moments worth promoting".
- List rows show "EP 2" instead of "Ep 2".
- The import error now names FrameCue.
- The excerpt test used to find the highlighted line through CSS class names. It now uses a
  `data-active` attribute, so it no longer breaks on styling changes.
- The sample-header assertion now reads FrameCue.

### Verified (redesign)
- **Frontend:** the TypeScript check, 58 Vitest tests and the production build pass.
- **Playwright locally:** 5 passed, plus the 2 private real-pack specs, run locally only.
- **Python:** 308 tests pass with the private pack (284 plus 24 skipped without it). Ruff is
  clean.
- **Vercel preview** (protected, checked in the browser pane):
  - the sample analyzes;
  - the `A` shortcut works;
  - reloading `/review` restores the selection and the decision.
- **Production** (https://promolens-two.vercel.app, checked without signing in):
  - the title is FrameCue, and the favicon and apple-touch icon are served;
  - deep links return 200, and unknown `/api` paths return a JSON 404;
  - the Playwright suite (5 tests) passes against production;
  - the served bundles and the workspace chunk contain no confidential names or key strings.

## Timeline redesign and AI coverage check (2026-11-11)
- **Timeline:** redesigned as a screenplay map (PLAN §19). The timing maths are unchanged.
- **Bug found during QA:** absolutely positioned screen-reader-only text escaped the detail
  scroll pane. The document became 37 px taller than the viewport, so the top bar scrolled
  away when chips were clicked. Fixed by making the panes `relative`, then re-measured:
  document height equals viewport height.
- **"AI reviewed 0 / 788":**
  - Reproduced with the real pack: 0 / 788 without a bundle (source `none`), 41 / 788 with
    the local bundle.
  - Every count agrees: the header, the evaluations, the statuses, the AI view (28 primaries
    plus 13 alternatives) and the per-episode counts.
  - Not a bug. The popover copy now explains the no-bundle case.
- **Tests added:** stepping through events in screenplay order with exact timecodes, and the
  no-bundle explanation.
