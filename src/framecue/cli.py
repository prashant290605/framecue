"""FrameCue command line (`framecue`).

    framecue analyze <pack.zip | episode.txt | folder> [--out DIR]
    framecue serve [--host 127.0.0.1] [--port 8000]
    framecue export-ai [--out runs/ai_bundle.json]   (private AI results for /api/analyze)
    framecue evaluate ...   (explicit opt-in live AI evaluation; see eval_driver)

``analyze`` is always offline. It never creates a model client, and it reuses cached AI
evaluations only when their keys match this input and the AI profile exactly. The console
shows counts, ids and scores, never screenplay text.

Exit codes: 0 ok, 2 bad arguments or unreadable input, 3 no valid episodes, 4 serve error.
"""

import argparse
import re
import sys
import time
from pathlib import Path

from framecue.exports import DEFAULT_RAW_DIR, write_exports
from framecue.llm_client import LLMSettings
from framecue.pipeline import (
    DEFAULT_CACHE_DIR,
    NoEpisodesError,
    analyze_path,
    coverage,
    run_summary,
)
from framecue.ranking import page

DEFAULT_OUT_ROOT = Path("runs/outputs")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["evaluate"]:  # pass everything after it to the live-evaluation driver
        from framecue import eval_driver

        return eval_driver.main(argv[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return _analyze(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "export-ai":
        return _export_ai(args)
    parser.print_help()
    return 2


def _analyze(args) -> int:
    source = Path(args.input)
    if not source.exists():
        print(f"error: input not found: {source}", file=sys.stderr)
        return 2
    if source.is_file() and source.suffix.lower() not in (".zip", ".txt"):
        print("error: input must be a .zip, a .txt or a folder of .txt files", file=sys.stderr)
        return 2
    settings = LLMSettings.from_env() if args.ai_profile == "env" else None
    started = time.perf_counter()
    try:
        run = analyze_path(
            source, cache_dir=None if args.no_cache else Path(args.cache_dir), ai_settings=settings
        )
    except NoEpisodesError as exc:
        print(f"error: no valid episodes in input; issue codes: {exc.codes}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: cannot read input ({type(exc).__name__})", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else DEFAULT_OUT_ROOT / _slug(source, run.input.sha256)
    manifest = write_exports(run, out, raw_dir=Path(args.raw_dir))
    elapsed = time.perf_counter() - started
    summary, cov = run_summary(run), coverage(run)
    print("FrameCue offline analysis (no model API calls)")
    print(
        f"input: {summary.input.name} ({summary.input.kind}, {summary.input.size_bytes:,} bytes,"
        f" sha256 {summary.input.sha256[:12]})"
    )
    print(
        f"episodes {summary.episodes} | scenes {summary.scenes} | events {summary.events} | "
        f"candidates {summary.candidates} | parse errors {summary.parse.errors}, "
        f"warnings {summary.parse.warnings}"
    )
    state = "complete" if cov.complete else "INCOMPLETE - the AI view is provisional"
    print(
        f"AI coverage: {cov.ai_evaluated}/{cov.total_candidates} candidates "
        f"({cov.candidate_coverage:.1%}), {cov.episodes_with_ai}/{cov.total_episodes} episodes "
        f"[{state}]"
    )
    a = cov.cache
    print(
        f"AI profile: {cov.profile.provider} {cov.profile.model} "
        f"reasoning={cov.profile.reasoning_effort} prompt={cov.profile.prompt_version}"
    )
    print(
        f"cache: {a.entries_scanned} scanned, {a.used} used, {a.stale_prompt_version} older "
        f"prompt, {a.other_profile_or_input} other profile/input, "
        f"{len(a.invalid_on_revalidation)} rejected, {a.not_in_this_input} other episodes"
    )
    top = page(run, "ai", limit=args.top).items
    if top:
        print(f"top {len(top)} AI-reviewed picks (rank, id, score/100):")
        for p in top:
            print(f"  {p.rank:>3}  {p.candidate_id}  {p.score.value:5.1f}")
    else:
        print("no AI-reviewed picks (no valid cached AI evaluations for this input)")
    cov_top = page(run, "coverage", limit=args.top).items
    print(f"top {len(cov_top)} baseline suggestions (unverified; rank, id, priority/1):")
    for p in cov_top:
        print(f"  {p.rank:>3}  {p.candidate_id}  {p.score.value:.3f}")
    print(f"outputs: {out} ({len(manifest.files) + 1} files, manifest.json)")
    print(f"time: {elapsed:.1f} s")
    return 0


def _serve(args) -> int:
    try:
        import uvicorn

        from framecue.api import ApiConfig, create_app
    except ImportError as exc:  # pragma: no cover - dependency missing
        print(f"error: web dependencies missing ({exc.name})", file=sys.stderr)
        return 4
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: binding beyond localhost exposes confidential runs", file=sys.stderr)
    config = ApiConfig(store_dir=Path(args.store_dir), cache_dir=Path(args.cache_dir))
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="info")
    return 0


def _export_ai(args) -> int:
    """Write the private AI cache as one bundle for upload to the stateless endpoint."""
    import json

    from framecue.evaluator import EvaluationCache

    cache = EvaluationCache(Path(args.cache_dir))
    if not cache.exists():
        print(f"error: no AI cache at {args.cache_dir}", file=sys.stderr)
        return 2
    bundle = cache.export_bundle()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")
    print(
        f"wrote {len(bundle['entries'])} AI assessments and {len(bundle['failures'])} failure "
        f"records to {out} ({out.stat().st_size:,} bytes)"
    )
    print("PRIVATE: it contains model explanations about confidential scripts. Do not publish.")
    return 0


def _slug(source: Path, sha256: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", source.stem).strip("-")[:40] or "input"
    return f"{stem}-{sha256[:8]}"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="framecue", description="FrameCue promo clip finder.")
    sub = p.add_subparsers(dest="command")

    a = sub.add_parser("analyze", help="offline analysis of a ZIP, .txt file or folder")
    a.add_argument("input")
    a.add_argument("--out", help="output directory (default runs/outputs/<input>-<sha>)")
    a.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    a.add_argument("--no-cache", action="store_true", help="ignore cached AI evaluations")
    a.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    a.add_argument(
        "--ai-profile",
        choices=["groq-default", "env"],
        default="groq-default",
        help="inference profile whose cached evaluations are reused",
    )
    a.add_argument("--top", type=int, default=5, help="picks to print per view")

    s = sub.add_parser("serve", help="run the local FastAPI backend")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--store-dir", default="runs/api")
    s.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))

    x = sub.add_parser("export-ai", help="bundle the private AI cache for /api/analyze uploads")
    x.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    x.add_argument("--out", default="runs/ai_bundle.json")

    sub.add_parser("evaluate", help="opt-in LIVE AI evaluation; run 'evaluate --help'")
    return p


if __name__ == "__main__":
    sys.exit(main())
