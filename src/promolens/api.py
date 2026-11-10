"""Local FastAPI backend for the PromoLens React frontend. Offline: no endpoint calls a model.

Routes (all JSON unless noted):

    GET  /api/health
    POST /api/runs                                     multipart "files": one .zip or .txt files
    GET  /api/runs/{run_id}                            RunSummary
    GET  /api/runs/{run_id}/coverage                   EvaluationCoverage
    GET  /api/runs/{run_id}/episodes                   list[EpisodeSummary]
    GET  /api/runs/{run_id}/promos?view=ai|coverage    PromoPage (filters, pagination)
    GET  /api/runs/{run_id}/promos/{candidate_id}      PromoDetail (with timeline events)
    GET  /api/runs/{run_id}/export                     ExportManifest
    GET  /api/runs/{run_id}/export/{file_name}         file download (fixed allowlist)

Errors always look like ``{"error": {"code": ..., "message": ...}}``. They never contain
stack traces, request bodies or screenplay text.

Security posture (local prototype):
- Run ids are 128-bit random and are never listed.
- Uploads are size-limited and parsed in memory with the ZIP safety rules.
- File names are labels only; no client-supplied path ever touches the filesystem.
- CORS allows only the configured local origins.
- Responses are sent with ``Cache-Control: no-store``.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from promolens import __version__
from promolens.document import AnalysisDocument, build_document
from promolens.evaluator import BundleError, MemoryCache
from promolens.exports import DEFAULT_RAW_DIR, write_exports
from promolens.llm_client import LLMSettings, _read_dotenv
from promolens.parser import parse_files, parse_zip_bytes
from promolens.pipeline import (
    DEFAULT_CACHE_DIR,
    NoEpisodesError,
    RunResult,
    analyze_parsed,
    coverage,
    describe_bytes,
    run_summary,
)
from promolens.ranking import PromoFilter, detail, episode_summaries, page
from promolens.results import (
    AIStatus,
    EpisodeSummary,
    EvaluationCoverage,
    ExportManifest,
    InputInfo,
    PromoDetail,
    PromoPage,
    RunSummary,
)
from promolens.schemas import ParseResult, QualityWarning

log = logging.getLogger("promolens.api")

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
CANDIDATE_ID = re.compile(r"^C\d{3,}-\d{7}-\d{7}$")
EXPORT_FILES = (
    "manifest.json",
    "episodes.json",
    "candidates.jsonl",
    "ai_evaluations.jsonl",
    "raw_model_outputs.jsonl",
    "rankings_ai.json",
    "rankings_coverage.json",
    "ai_recommendations.csv",
    "coverage_candidates.csv",
)
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ApiConfig:
    store_dir: Path = Path("runs/api")
    cache_dir: Path | None = DEFAULT_CACHE_DIR
    raw_dir: Path | None = DEFAULT_RAW_DIR
    max_upload_bytes: int = 25 * 1024 * 1024
    max_files: int = 120
    max_runs_in_memory: int = 8
    max_runs_on_disk: int = 30
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: _origins_from_env())
    ai_settings: LLMSettings | None = None


def _origins_from_env() -> tuple[str, ...]:
    """Explicit origins from the environment, then ``.env``; otherwise the local Vite ones."""
    raw = os.environ.get("PROMOLENS_CORS_ORIGINS") or _read_dotenv(Path(".env")).get(
        "PROMOLENS_CORS_ORIGINS", ""
    )
    origins = tuple(o.strip() for o in raw.split(",") if o.strip())
    return origins or DEFAULT_ORIGINS


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


# ---------------------------------------------------------------------------
# Run store: memory LRU + private on-disk copies under store_dir (gitignored runs/)
# ---------------------------------------------------------------------------


class RunStore:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.root = Path(config.store_dir)
        self._memory: OrderedDict[str, RunResult] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, run: RunResult) -> None:
        folder = self.root / run.run_id
        folder.mkdir(parents=True, exist_ok=False)
        state = {
            "run_id": run.run_id,
            "created_at": run.created_at,
            "input": run.input.model_dump(mode="json"),
            "parse": run.parse.model_dump(mode="json"),
        }
        (folder / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
        write_exports(run, folder / "exports", raw_dir=self.config.raw_dir)
        with self._lock:
            self._remember(run)
        self._prune_disk()

    def get(self, run_id: str) -> RunResult | None:
        if not RUN_ID.match(run_id):
            return None
        with self._lock:
            run = self._memory.get(run_id)
            if run is not None:
                self._memory.move_to_end(run_id)
                return run
        state_file = self.root / run_id / "run_state.json"
        if not state_file.is_file():
            return None
        state = json.loads(state_file.read_text(encoding="utf-8"))
        run = analyze_parsed(
            ParseResult.model_validate(state["parse"]),
            InputInfo.model_validate(state["input"]),
            cache_dir=self.config.cache_dir,
            ai_settings=self.config.ai_settings,
            run_id=run_id,
        )
        run.created_at = state["created_at"]
        with self._lock:
            self._remember(run)
        return run

    def export_dir(self, run_id: str) -> Path:
        return self.root / run_id / "exports"

    def _remember(self, run: RunResult) -> None:
        self._memory[run.run_id] = run
        self._memory.move_to_end(run.run_id)
        while len(self._memory) > self.config.max_runs_in_memory:
            self._memory.popitem(last=False)

    def _prune_disk(self) -> None:
        folders = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and RUN_ID.match(p.name)),
            key=lambda p: p.stat().st_mtime,
        )
        for old in folders[: max(0, len(folders) - self.config.max_runs_on_disk)]:
            shutil.rmtree(old, ignore_errors=True)
            with self._lock:
                self._memory.pop(old.name, None)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: ApiConfig | None = None) -> FastAPI:
    config = config or ApiConfig()
    store = RunStore(config)
    app = FastAPI(
        title="PromoLens API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.store = store
    app.state.config = config
    install_common(
        app,
        allowed_origins=config.allowed_origins,
        max_body_bytes=config.max_upload_bytes + _CHUNK,
        too_large_message=_too_large(config.max_upload_bytes),
    )
    local_limits = AnalyzeLimits(
        max_upload_bytes=config.max_upload_bytes, max_files=config.max_files
    )

    def get_run(run_id: str) -> RunResult:
        run = store.get(run_id)
        if run is None:
            raise ApiError(404, "run_not_found", "unknown run id")
        return run

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "offline": True, "live_ai": False}

    @app.post("/api/analyze", response_model=AnalysisDocument)
    def analyze(
        files: Annotated[list[UploadFile], File()],
        ai_bundle: Annotated[UploadFile | None, File()] = None,
    ) -> Response:
        """Stateless: analyze the upload and return the whole document. Nothing is stored.
        Locally this also reuses the private AI cache; an uploaded bundle takes precedence."""
        return analyze_request(files, ai_bundle, local_limits, config.cache_dir, config.ai_settings)

    @app.post("/api/runs", status_code=201, response_model=RunSummary)
    def create_run(files: Annotated[list[UploadFile], File()]) -> RunSummary:
        started = time.perf_counter()
        parsed, info, _ = read_upload(files, None, local_limits)
        parse_ms = int((time.perf_counter() - started) * 1000)
        try:
            run = analyze_parsed(
                parsed,
                info,
                cache_dir=config.cache_dir,
                ai_settings=config.ai_settings,
                parse_ms=parse_ms,
            )
        except NoEpisodesError as exc:
            raise ApiError(
                422, "no_valid_episodes", "no valid episode in upload", {"issue_codes": exc.codes}
            ) from None
        store.add(run)
        cov = coverage(run)
        log.info(
            "run created: %d episodes, %d candidates, %d AI-evaluated",
            len(run.episodes), len(run.by_id), cov.ai_evaluated,
        )  # fmt: skip
        return run_summary(run)

    @app.get("/api/runs/{run_id}", response_model=RunSummary)
    def get_summary(run_id: str) -> RunSummary:
        return run_summary(get_run(run_id))

    @app.get("/api/runs/{run_id}/coverage", response_model=EvaluationCoverage)
    def get_coverage(run_id: str) -> EvaluationCoverage:
        return coverage(get_run(run_id))

    @app.get("/api/runs/{run_id}/episodes", response_model=list[EpisodeSummary])
    def get_episodes(run_id: str) -> list[EpisodeSummary]:
        return episode_summaries(get_run(run_id))

    @app.get("/api/runs/{run_id}/promos", response_model=PromoPage)
    def get_promos(
        run_id: str,
        view: Literal["ai", "coverage"] = "ai",
        episode: Annotated[list[int] | None, Query()] = None,
        min_duration: Annotated[float | None, Query(ge=0, le=600)] = None,
        max_duration: Annotated[float | None, Query(ge=0, le=600)] = None,
        min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
        status: Annotated[list[AIStatus] | None, Query()] = None,
        warning: Annotated[list[QualityWarning] | None, Query()] = None,
        exclude_warning: Annotated[list[QualityWarning] | None, Query()] = None,
        include_alternatives: bool = False,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PromoPage:
        run = get_run(run_id)
        if episode and min(episode) < 1:
            raise ApiError(422, "invalid_request", "episode numbers start at 1")
        if view == "coverage" and min_score is not None and min_score > 1:
            raise ApiError(
                422, "invalid_request", "min_score for the coverage view is a 0-1 priority"
            )
        f = PromoFilter(
            episodes=frozenset(episode) if episode else None,
            min_duration_s=min_duration,
            max_duration_s=max_duration,
            min_score=min_score,
            statuses=frozenset(status) if status else None,
            warnings_any=frozenset(w.value for w in warning) if warning else None,
            exclude_warnings=frozenset(w.value for w in exclude_warning)
            if exclude_warning
            else None,
            include_alternatives=include_alternatives,
        )
        return page(run, view, f, offset=offset, limit=limit)

    @app.get("/api/runs/{run_id}/promos/{candidate_id}", response_model=PromoDetail)
    def get_promo(run_id: str, candidate_id: str) -> PromoDetail:
        run = get_run(run_id)
        if not CANDIDATE_ID.match(candidate_id) or candidate_id not in run.by_id:
            raise ApiError(404, "candidate_not_found", "unknown candidate id for this run")
        return detail(run, candidate_id)

    @app.get("/api/runs/{run_id}/export", response_model=ExportManifest)
    def get_export(run_id: str) -> ExportManifest:
        get_run(run_id)
        manifest = store.export_dir(run_id) / "manifest.json"
        if not manifest.is_file():
            raise ApiError(404, "export_not_found", "exports are not available for this run")
        return ExportManifest.model_validate_json(manifest.read_text(encoding="utf-8"))

    @app.get("/api/runs/{run_id}/export/{file_name}")
    def download(run_id: str, file_name: str) -> FileResponse:
        get_run(run_id)
        if file_name not in EXPORT_FILES:
            raise ApiError(404, "export_not_found", "unknown export file")
        path = store.export_dir(run_id) / file_name
        if not path.is_file():
            raise ApiError(404, "export_not_found", "this export was not produced for the run")
        media = "text/csv" if file_name.endswith(".csv") else "application/json"
        return FileResponse(path, media_type=media, filename=file_name)

    return app


# ---------------------------------------------------------------------------
# Shared HTTP plumbing (used by the stateful local app and the stateless serverless app)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalyzeLimits:
    max_upload_bytes: int = 25 * 1024 * 1024
    max_files: int = 120
    max_zip_entries: int | None = None  # None: the parser's default
    max_decompressed_bytes: int | None = None
    max_episodes: int | None = None
    max_response_bytes: int | None = None
    too_large_hint: str = ""


def install_common(
    app: FastAPI,
    *,
    allowed_origins: tuple[str, ...],
    max_body_bytes: int,
    too_large_message: str,
) -> None:
    """CORS, request-size guard, sanitized errors and no-store headers for /api routes."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_body_bytes:
                return _error(413, "upload_too_large", too_large_message)
        try:
            response = await call_next(request)
        except Exception as exc:  # never leak tracebacks or data
            log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
            return _error(500, "internal_error", "internal error; see server log for the type")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        message = "not found" if exc.status_code == 404 else str(exc.detail)
        return _error(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        fields = [
            {"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return _error(422, "invalid_request", "request validation failed", {"fields": fields})


def analyze_request(
    files: list[UploadFile],
    ai_bundle: UploadFile | None,
    limits: AnalyzeLimits,
    cache_dir: Path | None,
    ai_settings: LLMSettings | None,
) -> Response:
    """Parse + analyze one upload and return the serialized AnalysisDocument. Offline."""
    started = time.perf_counter()
    parsed, info, bundle = read_upload(files, ai_bundle, limits)
    parse_ms = int((time.perf_counter() - started) * 1000)
    if limits.max_episodes is not None and len(parsed.episodes) > limits.max_episodes:
        raise ApiError(
            413,
            "too_many_episodes",
            f"at most {limits.max_episodes} episodes per online analysis{limits.too_large_hint}",
        )
    cache = None
    if bundle is not None:
        try:
            cache = MemoryCache(bundle)
        except BundleError as exc:
            raise ApiError(400, "invalid_ai_bundle", str(exc)) from None
    try:
        run = analyze_parsed(
            parsed,
            info,
            cache_dir=cache_dir,
            cache=cache,
            ai_settings=ai_settings,
            parse_ms=parse_ms,
        )
    except NoEpisodesError as exc:
        raise ApiError(
            422, "no_valid_episodes", "no valid episode in upload", {"issue_codes": exc.codes}
        ) from None
    body = build_document(run).model_dump_json().encode("utf-8")
    if limits.max_response_bytes is not None and len(body) > limits.max_response_bytes:
        raise ApiError(
            413,
            "analysis_too_large",
            f"the analysis exceeds the online response limit{limits.too_large_hint}",
        )
    log.info("analyze: %d episodes, %d candidates", len(run.episodes), len(run.by_id))
    return Response(content=body, media_type="application/json")


def read_upload(
    files: list[UploadFile], ai_bundle: UploadFile | None, limits: AnalyzeLimits
) -> tuple[ParseResult, InputInfo, object | None]:
    """Validate and read an upload within ``limits``. Returns the parse result, a description
    of the input and, if supplied, the decoded AI bundle JSON."""
    if not files:
        raise ApiError(400, "no_files", "upload one .zip or one or more .txt files")
    if len(files) > limits.max_files:
        raise ApiError(400, "too_many_files", f"at most {limits.max_files} files per run")
    named = [(_label(f.filename), f) for f in files]
    bad = [n for n, _ in named if not n.lower().endswith((".zip", ".txt"))]
    if bad:
        raise ApiError(400, "unsupported_file_type", "only .zip and .txt files are accepted")
    zips = [n for n, _ in named if n.lower().endswith(".zip")]
    if zips and len(named) > 1:
        raise ApiError(400, "mixed_inputs", "upload either one .zip or only .txt files")
    budget = [limits.max_upload_bytes]

    def read_all(upload: UploadFile) -> bytes:
        chunks = []
        while chunk := upload.file.read(_CHUNK):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                message = _too_large(limits.max_upload_bytes) + limits.too_large_hint
                raise ApiError(413, "upload_too_large", message)
            chunks.append(chunk)
        return b"".join(chunks)

    contents = [(name, read_all(upload)) for name, upload in named]
    bundle = None
    if ai_bundle is not None:
        if not _label(ai_bundle.filename).lower().endswith(".json"):
            raise ApiError(400, "invalid_ai_bundle", "the AI bundle must be a .json file")
        try:
            bundle = json.loads(read_all(ai_bundle).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_ai_bundle", "the AI bundle is not valid JSON") from None
    if zips:
        name, data = contents[0]
        if not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise ApiError(400, "not_a_zip", "the .zip file is not a ZIP archive")
        parsed = parse_zip_bytes(
            data,
            name,
            max_entries=limits.max_zip_entries,
            max_total_bytes=limits.max_decompressed_bytes,
        )
        return parsed, describe_bytes(name, data, "zip"), bundle
    if len(contents) == 1:
        name, data = contents[0]
        return parse_files(contents, name), describe_bytes(name, data, "txt"), bundle
    blob = b"".join(n.encode("utf-8") + b"\0" + d for n, d in sorted(contents))
    info = describe_bytes("upload", blob, "files", files=len(contents))
    info = info.model_copy(update={"size_bytes": sum(len(d) for _, d in contents)})
    return parse_files(contents, "upload"), info, bundle


def _label(filename: str | None) -> str:
    """Base name only, printable, bounded. Used as a label, never as a path."""
    name = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:120]
    return name or "upload"


def _too_large(max_bytes: int) -> str:
    return f"upload exceeds {max_bytes / 1_000_000:.1f} MB"


def _error(status: int, code: str, message: str, extra: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, **(extra or {})}},
        headers={"Cache-Control": "no-store"},
    )
