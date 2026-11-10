# FrameCue

FrameCue is an editorial workspace for discovering, evaluating and reviewing promotional moments
in microdrama screenplays. It reads timestamped screenplays and proposes 20–60 second promo
clips, with exact segment timestamps, hook and cliffhanger evidence, and reasons.

The project was first built as PromoLens. The Python package, the `promolens` command and the
`PROMOLENS_*` settings keep that name, so existing commands, saved analyses and exports still
work.
- **Deterministic code:** hard constraints (timing, boundaries, validation), candidate
  discovery and the baseline.
- **Optional AI review:** a free, swappable LLM (GroqCloud `openai/gpt-oss-120b`) adds
  editorial judgement. Its output is used only after every cited event is validated.

Built for the Kuku AI Generalist take-home (Problem 1). The other docs:
- [docs/PLAN.md](docs/PLAN.md): the architecture contract;
- [docs/API.md](docs/API.md): the HTTP contract for the frontend;
- [docs/AI_LOG.md](docs/AI_LOG.md): the AI-assistance log.

## Status

| Phase | State |
|---|---|
| 0–2: Audit, parser, candidates, heuristic baseline | Done |
| 3–3.5: Evidence-grounded AI evaluator, live validation on the GroqCloud Free Plan ($0) | Done |
| 4: Full-pack pipeline, two ranking views, exports, CLI, FastAPI backend | Done |
| 4b: Vercel adaptation (stateless `/api/analyze`, one project and URL) | Done |
| 5: React + TypeScript + Vite + Tailwind + shadcn/ui editorial workspace | Done |
| 6: Final QA, release and submission | Done; production deployed and verified |

**Live demo:** https://promolens-two.vercel.app. It is public and ships only the code and a fictional
three-episode sample. Click **Try the fictional sample**, or upload your own screenplay files.

**AI coverage today: 41 of 788 candidates (5.2%) in 12 of 50 episodes.** These were
evaluated on the GroqCloud Free Plan within its daily token limit, at $0. The other 747
candidates have heuristic scores only, so the AI-reviewed ranking is *provisional*. Every
candidate in every episode is still available in the full-show baseline view.

## Quick start (Windows, PowerShell)

Requires Python 3.11+. Put the pack in `data\private\` (gitignored).

```bash
py -3.11 -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e ".[dev]"
```

```bash
.venv\Scripts\promolens analyze "data\private\pack.zip" --out runs\outputs\full-pack
```

`analyze` is **offline**. It never calls a model, needs no API key, and takes a few seconds
for the full pack (2–10 s measured, depending on machine load). It also works on a single new episode (`episode.txt`) or a folder of `.txt`
files. The console shows counts, IDs and scores, never screenplay text.

Exit codes: `0` success, `2` unreadable or unsupported input, `3` no valid episode.

Options:
- `--cache-dir` (default `runs\cache\evaluations`);
- `--no-cache` (baseline only);
- `--ai-profile env` (reuse evaluations made under the settings in `.env`);
- `--top N` (picks printed per view).

Equivalent module form: `.venv\Scripts\python -m promolens analyze ...`.

### Outputs (in the chosen private directory)

| File | What it is |
|---|---|
| `manifest.json` | Run summary, AI coverage, cache audit, notes, file list with sha256 |
| `rankings_ai.json` / `ai_recommendations.csv` | **AI-reviewed** recommendations: primaries plus alternative cuts |
| `rankings_coverage.json` / `coverage_candidates.csv` | **Full-show** baseline suggestions for all 788 candidates (unverified) |
| `candidates.jsonl` | Every candidate: segments, timestamps, baseline, AI judgement if any, ranks in both views |
| `ai_evaluations.jsonl` | Validated AI evaluations (derived from model output) |
| `raw_model_outputs.jsonl` | Unmodified model responses. Only written when they were captured; existing evaluations predate capture |
| `episodes.json` | Per-episode summary |

## Rankings and scoring provenance

There are two separate views. AI scores and heuristic priorities are **never ranked
together**.

- **AI-reviewed (`view=ai`, score `ai_weighted_v1`, 0–100).**
  - **Contents:** only candidates with a valid AI evaluation. The evaluation must have been
    made under the current prompt and inference profile, on the same episode file checksum,
    and its evidence must re-validate against the parsed screenplay.
  - **Order:** score, then cliffhanger, hook, standalone and pacing, then position.
  - **Provisional:** marked as such while coverage is incomplete.
- **Full-show coverage (`view=coverage`, score `heuristic_priority_v1`, 0–1).**
  - **Contents:** every shortlisted candidate.
  - **Order:** the deterministic baseline.
  - **Labelling:** marked as unverified suggestions, with no AI scores, explanations or
    confidence.
- **Near-duplicates.** In both views, a clip that overlaps a higher-ranked clip in the same
  episode (IoU ≥ 0.5, or ≥ 80 % of the shorter clip) becomes an unranked *alternative cut* of
  it. Nothing is forced into the top results, including episode endings.

Cache entries from older prompt versions or other inference profiles are excluded and
counted in the manifest. Today that is 24 older-prompt entries and 8 medium-reasoning
entries.

## Backend (FastAPI)

```bash
.venv\Scripts\promolens serve --port 8000
```

The server binds to `127.0.0.1`, serves OpenAPI docs at `/api/docs`, and allows CORS only
from `http://localhost:5173`. Main endpoints (full contract in [docs/API.md](docs/API.md)):

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Status (offline, no live AI) |
| POST | `/api/runs` | Upload one `.zip` or `.txt` files (multipart `files`); returns RunSummary |
| GET | `/api/runs/{id}` · `/coverage` · `/episodes` | Summary, AI coverage, per-episode summaries |
| GET | `/api/runs/{id}/promos?view=ai\|coverage` | Ranked page with filters and pagination |
| GET | `/api/runs/{id}/promos/{candidate_id}` | Detail with AI judgement, baseline and timeline events |
| GET | `/api/runs/{id}/export[/{file}]` | Export manifest, or one allowlisted file |

No endpoint runs model inference.

## Frontend (editorial workspace)

`frontend/` is a React 19 + TypeScript + Vite 8 app, styled with Tailwind 4 and
shadcn/ui-style components on Radix, with Lucide icons and minimal Motion. It talks only to
`GET /api/health` and `POST /api/analyze`.

**Design system.** All tokens live in `frontend/src/index.css`:
- **Colours:** a cool neutral scale (canvas `#F5F7FA`, surface `#FFFFFF`, ink `#142238`, muted
  `#64748B`, borders `#E2E8F0`) and one blue accent (`#345CE0`, hover `#2649BC`).
- **Type:** Manrope, self-hosted.
- **Hierarchy:** hairlines and spacing rather than shadows or nested cards.
- **Logo:** the mark (a split frame with a playhead cue) is drawn as SVG in
  `src/components/Brand.tsx` and `public/favicon.svg`.

**Upload screen:**
- **Inputs:** drag-and-drop or choose files, plus an optional AI bundle.
- **Validation:** type and size are checked against the limit reported by `/api/health`
  before anything is sent.
- **Feedback:** analysis can be cancelled, and errors are shown in plain language.
- **Try the fictional sample:** sends an original three-episode story (*The Harbor Light*,
  `frontend/public/sample/`, generated by `frontend/scripts/make_sample.py`) through the
  real API.

**Workspace:**
- **Layout:** a list and a detail view side by side on desktop; stacked list → detail on
  mobile.
- **Two separate views:**
  - **AI-reviewed:** genuine judgements only, 0–100, provisional while coverage is
    incomplete;
  - **Full show:** heuristic 0–1 priority, labelled unverified.
- **Navigation and filtering:** search, filters (episode, duration, status, warning,
  decision, alternative cuts) and a virtualized list.
- **Detail:**
  - exact `MM:SS.cc` timecodes and the score with its provenance;
  - hook and cliffhanger judgements with evidence chips;
  - an interactive **screenplay timeline**: real event timings, overlapping events packed
    into rows, the clip plus its first 3 s and last 5 s, ±10 s context;
  - the screenplay excerpt (before, in and after the clip), cuts of the same moment, and
    source references.
- **Review:** accept, later or reject (also with `A`/`L`/`R`), plus private notes.

**Saved in this browser (IndexedDB only).** The last analysis, your decisions, notes and
position survive a refresh.
- **Failure handling:** if storage is unavailable, full or corrupt, the analysis stays in
  memory and the UI says it is not saved.
- **Deleting:** "Delete saved analysis" removes everything.
- **Eviction:** browsers may clear this data on their own.
- **Not used:** cookies, localStorage and URLs.

**Exports (client-side):**
- ranked CSV, with the same columns as the CLI plus the editor's decision and note;
- editor shortlist (CSV or JSON);
- the full analysis JSON, which can be re-imported without being uploaded.

Exports can contain confidential screenplay text.

```bash
cd frontend && npm ci && npm run build
```

```bash
cd frontend && npm run dev
```

`npm run dev` serves on http://localhost:5173 and proxies `/api` to `promolens serve`
(port 8000).

## Deployment (Vercel, one project and one URL)

- **Entrypoint:** `app.py` (stateless FastAPI). Vercel's FastAPI support detects it.
- **Python:** 3.12, from `.python-version`.
- **Frontend:** `vercel.json` builds the Vite frontend (`frontend/`). `app.frontend()` serves `frontend/dist` from Vercel's CDN, with `index.html` as
  the fallback for deep links. API routes always take priority over frontend paths.
- **Routes:** only `GET /api/health` and `POST /api/analyze`. The local run store is not
  used, because the function filesystem is read-only and instances are not shared.
- **Payload:** uploads up to 4.0 MB. The full pack is 0.54 MB and its response 1.8 MB, under
  Vercel's 4.5 MB body limit. Larger inputs get a 413 pointing to the local CLI.
- **No inference:** the deployment never calls a model and ships no AI cache.
  - To show genuine AI results online, run `promolens export-ai` locally and upload
    `runs\ai_bundle.json` together with the screenplay. It is validated against the
    screenplay at request time.
- **Static files on the CDN:** `[tool.vercel.fastapi.static] cdn = true` in
  `pyproject.toml` serves the built frontend from Vercel's CDN. Without it, our middleware
  would keep the static files inside the Python function. Verified on the preview.
- **Upload allowlist:** `.vercelignore` uploads only `app.py`, `src/`, `frontend/` (no
  `node_modules`/`dist`), `pyproject.toml`, `README.md`, `vercel.json` and
  `.python-version`. It also blocks ZIPs, PDFs, `episode-*.txt`, `runs`, `outputs`, `data`,
  `.env*`, keys and AI bundles anywhere.
- **Uploaded files:** they are processed in memory by Vercel's infrastructure for the
  duration of the request. FrameCue does not store them, but they do pass through the
  platform.

Preview deployment (protected by Vercel Authentication):

```bash
npx vercel@65.4.0 deploy
```

Production deployment (public):

```bash
npx vercel@65.4.0 deploy --prod
```

The project's very first deployment went to production automatically and was removed; see
PLAN §17. The current production deployment was made on purpose for submission (Phase 6).

## Opt-in live AI evaluation (free quota)

Live evaluation is a separate, explicit command and never part of `analyze` or the API.

**Setup:**
1. Copy `.env.example` to `.env`.
2. Set a Groq key (Free Plan, Zero Data Retention enabled, no payment method).
3. Dry-run first, which plans only:

   ```bash
   .venv\Scripts\promolens evaluate --zip "data\private\pack.zip" --schedule coverage-first --levels 1
   ```

4. Add `--allow-large --live` to actually evaluate.

**How it runs:**
- `coverage-first` picks the best un-evaluated candidate from each episode that has no AI
  assessment yet (38 episodes today, about 107k tokens), before any alternatives.
  `--levels 2` adds a second per episode.
- **Guards:**
  - **Pacing:** requests are paced to Groq's free 8,000 tokens/minute and honour
    `Retry-After`.
  - **Daily budget:** a local daily token budget (default 180,000) applies. It is our own
    accounting by UTC day; the provider's reset time is not documented, so a daily 429 is
    authoritative.
  - **Paid tiers:** paid tiers are never requested.
- **Progress:** accepted results are cached, so the next `analyze` picks them up and an
  interrupted run resumes.
- **Raw responses:** unmodified responses are kept in `runs\raw\`.

Measured limits, token use and quality findings: [PLAN §10b](docs/PLAN.md).

## Testing

```bash
.venv\Scripts\python -m pytest
```

```bash
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format --check .
```

Private end-to-end tests on the real pack skip unless the path is given:

```bash
$env:PROMOLENS_TEST_ZIP="data\private\pack.zip"; .venv\Scripts\python -m pytest -m private
```

Mocked tests never contact a real API: network transports are patched to fail.
CI (`.github/workflows/ci.yml`) runs Ruff and pytest, then the frontend type check, build
and Vitest.

**Frontend:**
- **Unit tests:** Vitest and Testing Library, with fake IndexedDB and synthetic fixtures.
- **End-to-end:** Playwright against the real `app.py`, in the installed Microsoft Edge (no
  browser download).

```bash
cd frontend && npm test
```

```bash
cd frontend && npm run build && npx playwright test
```

The same specs run against a deployment with no local server, for example production:

```bash
cd frontend && $env:E2E_BASE_URL="https://promolens-two.vercel.app"; npx playwright test
```

`e2e/release.spec.ts` asserts only same-origin requests (no model or third-party calls), no
cookies or localStorage, both views, the timeline, deep-link reload, and export then
re-import.

`e2e/private.spec.ts` runs only when `PROMOLENS_TEST_ZIP` is set. It writes screenshots
and metrics to the gitignored `runs/screens/`.

## Known limitations

- **Partial AI coverage:** 5.2 % of candidates. The AI view is provisional, and most
  episodes have baseline suggestions only.
- **Subjective judgements:** AI scores are editorial estimates from one model on a small
  reviewed sample (PLAN §10b). They are not accuracy or conversion measurements. Explanation
  prose is not machine-checked; evidence IDs are.
- **No raw responses for past runs:** the 41 evaluations were stored as validated, parsed
  results. Unmodified responses (`runs
aw\`) are captured only for runs made after that
  feature was added.
- **Known AI failure modes**, from a rubric review of the top 10 picks by the AI coding
  assistant (not human labels):
  - explanations sometimes cite events just after the clip ends;
  - small embellishments at low reasoning effort;
  - compressed, generous scores on quiet scenes;
  - two heavily overlapping cuts (IoU 0.42) can both rank as primaries.
  
  See PLAN §18.
- **Prototype run store:** files under `runs\api\` (30 runs kept). Editor accept/reject
  decisions are not stored server-side.
- **Single segments:** v1 clips are one continuous segment; multi-segment montage is not
  generated.
- **Frontend persistence:** it is per browser and per device. A new analysis replaces the
  saved one, and there is no cross-device sync.
- **Function region:** the Vercel function runs in `iad1` (US East). From India a
  full-pack round trip is 6–7 s, with about 3 s of compute. Pinning a nearer region is a
  possible follow-up.

## Confidentiality

- The pack, `.env`, `runs\` (outputs, cache, uploads, raw model text) and `data\private\` are
  gitignored. Tests use invented fixtures only.
- **Model calls:** none happen without `--live`. Diagnostics, logs and console output never
  contain screenplay text. Secrets live only in `.env`.
- **API exposure:** the API binds to localhost, uses unguessable run IDs without a listing
  endpoint, limits upload size, parses ZIPs in memory with path-traversal, symlink and
  zip-bomb guards, and returns sanitized errors without tracebacks.
