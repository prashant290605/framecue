"""Parser for Kuku-style timestamped screenplays (format verified in docs/PLAN.md section 4).

Expected layout of one episode file::

    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. PLACE - DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    Actions and dialogue:
    **CAMERA:**
    Start: (00:00.00) | End: (00:04.80)
    One or more lines of text.
    --- SCRIPT END ---

Parsing happens in two passes. The first is a line-oriented pass that collects raw records and
structural problems. The second validates timing relationships. Pydantic models are only built
for an episode with no ERROR-level issue, so every returned ``Episode`` is internally
consistent. Events are never rounded, clamped, merged or reordered. Source order is kept, and
``Episode.timeline()`` gives the chronological view. Diagnostics give locations and codes and
never quote screenplay text.
"""

import hashlib
import io
import re
import stat
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from framecue.schemas import (
    Episode,
    EventType,
    ParseIssue,
    ParseResult,
    Scene,
    ScriptEvent,
    Severity,
    SourceLine,
)
from framecue.timecode import parse_timestamp

MAX_ENTRY_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_TOTAL_BYTES = 100 * 1024 * 1024  # decompressed bytes read from one archive
# Errors a damaged or hostile archive can raise while being opened or read.
_ARCHIVE_ERRORS = (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, OSError)

_SCRIPT_START = re.compile(r"^-{3,}\s*script\s+start\s*-{3,}$", re.IGNORECASE)
_SCRIPT_END = re.compile(r"^-{3,}\s*script\s+end\s*-{3,}$", re.IGNORECASE)
_HEADER_FIELD = re.compile(r"^([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$")
_EPISODE_NUMBER = re.compile(r"^(?:episode\s+)?(\d+)$", re.IGNORECASE)
_SCENE = re.compile(r"^scene\s+(\d+)$", re.IGNORECASE)
_SCENE_FIELD = re.compile(r"^scene\s+(heading|start|end)\s*:\s*(.*)$", re.IGNORECASE)
_ACTIONS_MARKER = re.compile(r"^actions\s+and\s+dialogue\s*:?$", re.IGNORECASE)
_LABEL = re.compile(r"^\*\*(.+?):\*\*$")
_TIMING_PREFIX = re.compile(r"^start\s*:", re.IGNORECASE)
_TIMING = re.compile(r"^start\s*:\s*\(([^()]*)\)\s*\|\s*end\s*:\s*\(([^()]*)\)$", re.IGNORECASE)
_SPEAKER = re.compile(r"^(?P<name>[^()]*?)\s*(?P<notes>(?:\([^()]*\)\s*)*)$")
_NOTE = re.compile(r"\(([^()]*)\)")
_FILENAME = re.compile(r"^episode-(\d+)-episode\s+(\S+?)\.txt$", re.IGNORECASE)

_HEADER_KEYS = ("EPISODE NUMBER", "EPISODE LOGLINE", "CONTINUITY NOTES")
_NON_DIALOGUE = {
    "CAMERA": EventType.CAMERA,
    "ACTION": EventType.ACTION,
    "SOUND FX": EventType.SOUND_FX,
    "SOUND_FX": EventType.SOUND_FX,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_path(path: str | Path) -> ParseResult:
    """Parse a ZIP archive, a directory of ``.txt`` files, or a single episode file."""
    path = Path(path)
    if path.is_dir():
        return parse_directory(path)
    if path.suffix.lower() == ".zip":
        return parse_zip(path)
    data = path.read_bytes()
    episode, issues = parse_episode_bytes(data, path.name)
    return _finalize(path.name, [episode] if episode else [], issues)


def parse_episode_text(text: str, source_name: str = "<text>") -> ParseResult:
    """Parse one episode from an already-decoded string."""
    episode, issues = _EpisodeParser(source_name).parse(text, _sha256(text.encode("utf-8")))
    return _finalize(source_name, [episode] if episode else [], issues)


def parse_episode_bytes(data: bytes, source_name: str) -> tuple[Episode | None, list[ParseIssue]]:
    """Decode strict UTF-8 (an optional BOM is accepted) and parse one episode."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        issue = _pack_issue(
            source_name, "invalid_utf8", f"file is not valid UTF-8 (byte offset {exc.start})"
        )
        return None, [issue]
    return _EpisodeParser(source_name).parse(text, _sha256(data))


def parse_directory(path: str | Path) -> ParseResult:
    """Parse every ``.txt`` file below ``path`` in sorted relative-path order."""
    root = Path(path)
    episodes: list[Episode] = []
    issues: list[ParseIssue] = []
    for file in sorted(root.rglob("*.txt"), key=lambda p: p.relative_to(root).as_posix()):
        name = file.relative_to(root).as_posix()
        if _is_ignorable(name):
            continue
        episode, file_issues = parse_episode_bytes(file.read_bytes(), name)
        issues.extend(file_issues)
        if episode:
            episodes.append(episode)
    return _finalize(root.name, episodes, issues)


def parse_zip(path: str | Path) -> ParseResult:
    """Read a screenplay ZIP in place, without extracting anything to disk.

    Entries are read in sorted name order. Unsafe entries are rejected as ERROR and never
    read: absolute paths, ``..`` traversal, drive letters, symlinks, encrypted or oversized
    entries, and duplicate names. Directory entries are skipped, and so are non-text files and
    macOS metadata (INFO).
    """
    path = Path(path)
    return _parse_zip_source(path, path.name)


def parse_zip_bytes(
    data: bytes,
    source_name: str,
    *,
    max_entries: int | None = None,
    max_total_bytes: int | None = None,
) -> ParseResult:
    """Parse an in-memory ZIP (for example an HTTP upload) with the same safety rules as
    ``parse_zip``. Nothing is written to disk. Callers such as the serverless endpoint may pass
    tighter entry-count and decompressed-size limits."""
    return _parse_zip_source(io.BytesIO(data), source_name, max_entries, max_total_bytes)


def parse_files(files: list[tuple[str, bytes]], source_name: str = "upload") -> ParseResult:
    """Parse several individually supplied episode files. Names are labels only; they are
    never used as filesystem paths. Non-``.txt`` names are skipped (INFO)."""
    episodes: list[Episode] = []
    issues: list[ParseIssue] = []
    for name, data in sorted(files, key=lambda item: item[0]):
        if _is_ignorable(name) or not name.lower().endswith(".txt"):
            issues.append(_pack_issue(name, "skipped_entry", "not a screenplay", Severity.INFO))
            continue
        episode, file_issues = parse_episode_bytes(data, name)
        issues.extend(file_issues)
        if episode:
            episodes.append(episode)
    return _finalize(source_name, episodes, issues)


def _parse_zip_source(
    source: str | Path | io.BytesIO,
    name: str,
    max_entries: int | None = None,
    max_total_bytes: int | None = None,
) -> ParseResult:
    try:
        with zipfile.ZipFile(source) as archive:
            episodes, issues = _parse_archive(archive, max_entries, max_total_bytes)
    except _ARCHIVE_ERRORS as exc:
        issue = _pack_issue(name, "unreadable_archive", type(exc).__name__)
        return _finalize(name, [], [issue])
    return _finalize(name, episodes, issues)


# ---------------------------------------------------------------------------
# Archive / pack handling
# ---------------------------------------------------------------------------


def _parse_archive(
    archive: zipfile.ZipFile, max_entries: int | None = None, max_total_bytes: int | None = None
) -> tuple[list[Episode], list[ParseIssue]]:
    entry_limit = MAX_ARCHIVE_ENTRIES if max_entries is None else max_entries
    byte_limit = MAX_ARCHIVE_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    infos = archive.infolist()
    if len(infos) > entry_limit:
        issue = _pack_issue("<archive>", "too_many_entries", f"{len(infos)} entries")
        return [], [issue]
    episodes: list[Episode] = []
    issues: list[ParseIssue] = []
    name_counts = Counter(info.filename for info in infos)
    total_read = 0
    for info in sorted(infos, key=lambda i: i.filename):
        name = info.filename
        if info.is_dir():
            continue
        problem = _unsafe_entry_reason(info, name_counts[name])
        if problem:
            issues.append(_pack_issue(name, "unsafe_archive_entry", problem))
            continue
        if _is_ignorable(name) or not name.lower().endswith(".txt"):
            issues.append(_pack_issue(name, "skipped_entry", "not a screenplay", Severity.INFO))
            continue
        if total_read + info.file_size > byte_limit:
            message = f"archive expands beyond {byte_limit} bytes; stopped reading"
            issues.append(_pack_issue(name, "archive_too_large", message))
            break
        data = _read_entry(archive, info)
        if data is None:
            issues.append(_pack_issue(name, "entry_too_large", f"exceeds {MAX_ENTRY_BYTES} B"))
            continue
        total_read += len(data)
        if total_read > byte_limit:  # under-declared sizes
            message = f"archive expands beyond {byte_limit} bytes; stopped reading"
            issues.append(_pack_issue(name, "archive_too_large", message))
            break
        episode, file_issues = parse_episode_bytes(data, name)
        issues.extend(file_issues)
        if episode:
            episodes.append(episode)
    return episodes, issues


def _unsafe_entry_reason(info: zipfile.ZipInfo, name_count: int) -> str | None:
    name = info.filename.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if "\x00" in name:
        return "entry name contains a NUL byte"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return "absolute entry path"
    if ".." in parts:
        return "path traversal ('..') in entry name"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "symbolic link entry"
    if info.flag_bits & 0x1:
        return "encrypted entry"
    if name_count > 1:
        return "duplicate entry name"
    if info.file_size > MAX_ENTRY_BYTES:
        return f"declared size exceeds {MAX_ENTRY_BYTES} bytes"
    return None


def _read_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    """Read at most MAX_ENTRY_BYTES, guarding against headers that under-declare size."""
    with archive.open(info) as handle:
        data = handle.read(MAX_ENTRY_BYTES + 1)
    return None if len(data) > MAX_ENTRY_BYTES else data


def _is_ignorable(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return "__MACOSX" in parts or parts[-1].startswith("._") or parts[-1] == ".DS_Store"


def _finalize(source: str, episodes: list[Episode], issues: list[ParseIssue]) -> ParseResult:
    """Apply pack-level checks: duplicate numbers (ERROR, all copies excluded) and gaps."""
    counts = Counter(episode.episode_number for episode in episodes)
    for episode in episodes:
        if counts[episode.episode_number] > 1:
            issues.append(
                _pack_issue(
                    episode.source_name,
                    "duplicate_episode_number",
                    f"episode {episode.episode_number} appears in {counts[episode.episode_number]}"
                    " files; all copies excluded",
                    episode_number=episode.episode_number,
                )
            )
    kept = sorted((e for e in episodes if counts[e.episode_number] == 1), key=_episode_key)
    if not episodes and not any(i.severity is Severity.ERROR for i in issues):
        issues.append(_pack_issue(source, "no_episodes", "no screenplay files found"))
    numbers = sorted(counts)
    missing = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers)) if numbers else []
    if missing:
        message = f"episode numbers missing from sequence: {missing}"
        issues.append(_pack_issue(source, "episode_sequence_gap", message, Severity.WARNING))
    return ParseResult(source=source, episodes=tuple(kept), issues=tuple(issues))


def _episode_key(episode: Episode) -> tuple[int, str]:
    return episode.episode_number, episode.source_name


def _pack_issue(
    source: str,
    code: str,
    message: str,
    severity: Severity = Severity.ERROR,
    episode_number: int | None = None,
) -> ParseIssue:
    return ParseIssue(
        severity=severity,
        code=code,
        message=message,
        source=source,
        episode_number=episode_number,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_lines(text: str) -> list[str]:
    """Split on CRLF/CR/LF only. ``str.splitlines`` would also split on characters such as
    U+2028 or form feeds that can appear inside prose."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


# ---------------------------------------------------------------------------
# Episode parsing
# ---------------------------------------------------------------------------


@dataclass
class _RawEvent:
    source_index: int
    label: str
    line: int
    scene_number: int
    timing_seen: bool = False
    start_ms: int | None = None
    end_ms: int | None = None
    text: list[str] = field(default_factory=list)


@dataclass
class _RawScene:
    number: int
    line: int
    fields: dict[str, str] = field(default_factory=dict)
    start_ms: int | None = None
    end_ms: int | None = None
    events: list[_RawEvent] = field(default_factory=list)
    unparsed: list[SourceLine] = field(default_factory=list)


@dataclass
class _Header:
    values: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)


class _EpisodeParser:
    """Single-use parser for one episode file. Collects issues instead of raising."""

    def __init__(self, source_name: str) -> None:
        self.source = source_name
        self.issues: list[ParseIssue] = []
        self.episode_number: int | None = None
        self.unparsed: list[SourceLine] = []

    # -- entry point -------------------------------------------------------

    def parse(self, text: str, sha256: str) -> tuple[Episode | None, list[ParseIssue]]:
        episode = self._parse(text, sha256)
        return episode, self.issues

    def _parse(self, text: str, sha256: str) -> Episode | None:
        lines = _split_lines(text)
        markers = self._find_markers(lines)
        if markers is None:
            return None
        start, end = markers
        header = self._parse_header(lines[:start])
        scenes = self._parse_body(lines, start + 1, end)
        self._collect_trailing(lines, end + 1)
        self._check_filename()
        self._validate(scenes)
        if self.episode_number is None or self._has_errors():
            return None
        return self._build(header, scenes, sha256)

    # -- diagnostics -------------------------------------------------------

    def _issue(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        line: int | None = None,
        scene: int | None = None,
        event: _RawEvent | None = None,
    ) -> None:
        self.issues.append(
            ParseIssue(
                severity=severity,
                code=code,
                message=message,
                source=self.source,
                line=line if line is not None else (event.line if event else None),
                episode_number=self.episode_number,
                scene_number=scene
                if scene is not None
                else (event.scene_number if event else None),
                event_id=self._event_id(event) if event else None,
            )
        )

    def _has_errors(self) -> bool:
        return any(issue.severity is Severity.ERROR for issue in self.issues)

    def _event_id(self, event: _RawEvent) -> str | None:
        if self.episode_number is None:
            return None
        return f"E{self.episode_number:03d}-S{event.scene_number:02d}-{event.source_index:04d}"

    # -- structure ---------------------------------------------------------

    def _find_markers(self, lines: list[str]) -> tuple[int, int] | None:
        starts = [i for i, line in enumerate(lines) if _SCRIPT_START.match(line.strip())]
        ends = [i for i, line in enumerate(lines) if _SCRIPT_END.match(line.strip())]
        ok = True
        for name, found in (("SCRIPT START", starts), ("SCRIPT END", ends)):
            if len(found) != 1:
                code = "missing_script_marker" if not found else "duplicate_script_marker"
                self._issue(
                    Severity.ERROR, code, f"expected one '{name}' marker, found {len(found)}"
                )
                ok = False
        if ok and ends[0] < starts[0]:
            self._issue(Severity.ERROR, "script_markers_reversed", "END marker precedes START")
            ok = False
        return (starts[0], ends[0]) if ok else None

    def _parse_header(self, lines: list[str]) -> _Header:
        header = _Header()
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped:
                continue
            match = _HEADER_FIELD.match(stripped)
            if match is None:
                self._unrecognized(raw, index + 1, None)
                continue
            key = " ".join(match.group(1).upper().split())
            value = match.group(2).strip()
            target = header.values if key in _HEADER_KEYS else header.extra
            if key in target:
                self._issue(Severity.ERROR, "duplicate_header_field", key, line=index + 1)
            elif target is header.extra:
                self._issue(
                    Severity.WARNING,
                    "unknown_header_field",
                    f"'{key}' kept in extra_metadata",
                    line=index + 1,
                )
            target[key] = value
        self._read_episode_number(header)
        for key in ("EPISODE LOGLINE", "CONTINUITY NOTES"):
            if not header.values.get(key):
                self._issue(Severity.WARNING, "missing_header_field", f"{key} is missing or empty")
        return header

    def _read_episode_number(self, header: _Header) -> None:
        value = header.values.get("EPISODE NUMBER")
        if value is None:
            self._issue(Severity.ERROR, "missing_episode_number", "EPISODE NUMBER is required")
            return
        match = _EPISODE_NUMBER.match(value)
        if match is None or int(match.group(1)) < 1:
            self._issue(
                Severity.ERROR,
                "invalid_episode_number",
                "EPISODE NUMBER must look like 'Episode N' with N >= 1",
            )
            return
        self.episode_number = int(match.group(1))

    def _check_filename(self) -> None:
        match = _FILENAME.match(PurePosixPath(self.source).name)
        if match and self.episode_number is not None and int(match.group(1)) != self.episode_number:
            self._issue(
                Severity.WARNING,
                "filename_episode_mismatch",
                f"filename says episode {int(match.group(1))}, header says {self.episode_number}",
            )

    def _collect_trailing(self, lines: list[str], start: int) -> None:
        for index in range(start, len(lines)):
            if lines[index].strip():
                self._unrecognized(lines[index], index + 1, None)

    # -- body --------------------------------------------------------------

    def _parse_body(self, lines: list[str], start: int, end: int) -> list[_RawScene]:
        """Line-oriented state machine over the lines between the script markers.

        A blank line ends an event's text block once the block has some text. Blank lines
        between a label, its timing line and its first text line are tolerated.
        """
        scenes: list[_RawScene] = []
        current: _RawEvent | None = None
        next_index = 0
        for index in range(start, end):
            raw, line_no = lines[index], index + 1
            stripped = raw.strip()
            scene = scenes[-1] if scenes else None
            if not stripped:
                if current is not None and current.text:
                    current = None
                continue
            if match := _SCENE.match(stripped):
                current = None
                self._start_scene(scenes, int(match.group(1)), line_no)
            elif match := _SCENE_FIELD.match(stripped):
                current = None
                self._set_scene_field(scene, match.group(1).lower(), match.group(2), line_no)
            elif _ACTIONS_MARKER.match(stripped):
                current = None
            elif match := _LABEL.match(stripped):
                current = self._start_event(scene, match.group(1).strip(), line_no, next_index)
                next_index += 1
            elif _TIMING_PREFIX.match(stripped):
                self._set_timing(current, stripped, line_no)
            elif current is not None:
                self._add_text(current, stripped, line_no)
            else:
                self._unrecognized(raw, line_no, scene)
        return scenes

    def _start_scene(self, scenes: list[_RawScene], number: int, line: int) -> None:
        if any(scene.number == number for scene in scenes):
            self._issue(Severity.ERROR, "duplicate_scene_number", f"scene {number}", line=line)
        else:
            expected = scenes[-1].number + 1 if scenes else 1
            if number != expected:
                message = f"scene {number} found where scene {expected} was expected"
                self._issue(Severity.WARNING, "scene_number_sequence", message, line=line)
        scenes.append(_RawScene(number=number, line=line))

    def _set_scene_field(self, scene: _RawScene | None, key: str, value: str, line: int) -> None:
        if scene is None:
            self._issue(Severity.ERROR, "scene_field_outside_scene", key, line=line)
            return
        if key in scene.fields:
            self._issue(Severity.ERROR, "duplicate_scene_field", key, line=line, scene=scene.number)
            return
        scene.fields[key] = value.strip()
        if key == "heading":
            return
        try:
            millis = parse_timestamp(value)
        except ValueError:
            message = f"scene {key} is not MM:SS.cc"
            self._issue(
                Severity.ERROR, "malformed_timestamp", message, line=line, scene=scene.number
            )
            return
        if key == "start":
            scene.start_ms = millis
        else:
            scene.end_ms = millis

    def _start_event(
        self, scene: _RawScene | None, label: str, line: int, source_index: int
    ) -> _RawEvent | None:
        if scene is None:
            self._issue(
                Severity.ERROR, "event_outside_scene_block", "event before any SCENE", line=line
            )
            return None
        event = _RawEvent(
            source_index=source_index, label=label, line=line, scene_number=scene.number
        )
        scene.events.append(event)
        return event

    def _set_timing(self, event: _RawEvent | None, stripped: str, line: int) -> None:
        if event is None or event.timing_seen:
            self._issue(
                Severity.ERROR,
                "unexpected_timing_line",
                "timing line without an open event",
                line=line,
            )
            return
        event.timing_seen = True
        match = _TIMING.match(stripped)
        if match is None:
            message = "expected 'Start: (MM:SS.cc) | End: (MM:SS.cc)'"
            self._issue(Severity.ERROR, "malformed_timing_line", message, line=line, event=event)
            return
        try:
            event.start_ms = parse_timestamp(match.group(1))
            event.end_ms = parse_timestamp(match.group(2))
        except ValueError:
            message = "timestamp is not MM:SS.cc"
            self._issue(Severity.ERROR, "malformed_timestamp", message, line=line, event=event)

    def _add_text(self, event: _RawEvent, stripped: str, line: int) -> None:
        if not event.timing_seen:
            message = "text appears before the timing line"
            self._issue(Severity.ERROR, "event_text_before_timing", message, line=line, event=event)
        event.text.append(stripped)

    def _unrecognized(self, raw: str, line: int, scene: _RawScene | None) -> None:
        """Keep the line verbatim; a bold line that is not a valid label is an ERROR because
        it is probably a mangled event."""
        preserved = SourceLine(line=line, text=raw.rstrip())
        (scene.unparsed if scene else self.unparsed).append(preserved)
        scene_no = scene.number if scene else None
        if raw.strip().startswith("**"):
            message = "line looks like an event label but is not '**LABEL:**'"
            self._issue(Severity.ERROR, "malformed_event_label", message, line=line, scene=scene_no)
        else:
            message = f"unrecognized line ({len(raw.strip())} chars) preserved in unparsed_lines"
            self._issue(Severity.WARNING, "unrecognized_line", message, line=line, scene=scene_no)

    # -- validation --------------------------------------------------------

    def _validate(self, scenes: list[_RawScene]) -> None:
        if not scenes:
            self._issue(Severity.ERROR, "no_scenes", "episode has no SCENE blocks")
            return
        for scene in scenes:
            self._validate_scene(scene)
            for event in scene.events:
                self._validate_event(event, scene)
        self._check_scene_continuity(scenes)
        timed = [
            e for s in scenes for e in s.events if e.start_ms is not None and e.end_ms is not None
        ]
        self._check_source_order(timed)
        self._check_same_type_overlap(timed)

    def _validate_scene(self, scene: _RawScene) -> None:
        for key in ("heading", "start", "end"):
            if not scene.fields.get(key):
                message = f"scene {key} is missing"
                self._issue(
                    Severity.ERROR,
                    "missing_scene_field",
                    message,
                    line=scene.line,
                    scene=scene.number,
                )
        if (
            scene.start_ms is not None
            and scene.end_ms is not None
            and scene.end_ms <= scene.start_ms
        ):
            message = "scene end is not after scene start"
            self._issue(
                Severity.ERROR,
                "invalid_scene_duration",
                message,
                line=scene.line,
                scene=scene.number,
            )
        if not scene.events:
            self._issue(
                Severity.WARNING,
                "empty_scene",
                "scene has no events",
                line=scene.line,
                scene=scene.number,
            )

    def _validate_event(self, event: _RawEvent, scene: _RawScene) -> None:
        if not event.timing_seen:
            self._issue(Severity.ERROR, "event_missing_timing", "no timing line", event=event)
        if not event.text:
            self._issue(Severity.ERROR, "event_missing_text", "no text after timing", event=event)
        if event.start_ms is None or event.end_ms is None:
            return
        if event.end_ms < event.start_ms:
            message = f"end is {event.start_ms - event.end_ms} ms before start"
            self._issue(Severity.ERROR, "reversed_event_duration", message, event=event)
        elif event.end_ms == event.start_ms:
            self._issue(Severity.WARNING, "zero_duration_event", "start equals end", event=event)
        self._check_within_scene(event, scene)
        if _classify(event.label) is EventType.DIALOGUE:
            self._check_dialogue(event)

    def _check_within_scene(self, event: _RawEvent, scene: _RawScene) -> None:
        assert event.start_ms is not None and event.end_ms is not None
        if scene.start_ms is not None and event.start_ms < scene.start_ms:
            message = f"starts {scene.start_ms - event.start_ms} ms before its scene start"
            self._issue(Severity.WARNING, "event_outside_scene", message, event=event)
        if scene.end_ms is not None and event.end_ms > scene.end_ms:
            message = f"ends {event.end_ms - scene.end_ms} ms after its scene end"
            self._issue(Severity.WARNING, "event_outside_scene", message, event=event)

    def _check_dialogue(self, event: _RawEvent) -> None:
        if _split_speaker(event.label) is None:
            message = "speaker annotations not parseable; whole label used as speaker"
            self._issue(Severity.WARNING, "speaker_label_unparsed", message, event=event)
        text = " ".join(event.text)
        if not (text.startswith('"') and text.endswith('"')):
            message = "dialogue text is not in double quotes (possible unknown event category)"
            self._issue(Severity.WARNING, "dialogue_unquoted", message, event=event)

    def _check_scene_continuity(self, scenes: list[_RawScene]) -> None:
        timed = [s for s in scenes if s.start_ms is not None and s.end_ms is not None]
        if timed and timed[0].start_ms != 0:
            message = f"first scene starts at {timed[0].start_ms} ms, not 0"
            self._issue(
                Severity.WARNING, "episode_not_starting_at_zero", message, scene=timed[0].number
            )
        for previous, scene in zip(timed, timed[1:], strict=False):
            assert previous.end_ms is not None and scene.start_ms is not None
            delta = scene.start_ms - previous.end_ms
            if delta > 0:
                message = f"{delta} ms gap after previous scene"
                self._issue(
                    Severity.WARNING, "scene_gap", message, line=scene.line, scene=scene.number
                )
            elif delta < 0:
                message = f"overlaps previous scene by {-delta} ms"
                self._issue(
                    Severity.ERROR, "scene_overlap", message, line=scene.line, scene=scene.number
                )

    def _check_source_order(self, events: list[_RawEvent]) -> None:
        """WARN when an event starts earlier than an event listed before it."""
        latest_start = -1
        for event in events:
            assert event.start_ms is not None
            if event.start_ms < latest_start:
                message = (
                    f"starts {latest_start - event.start_ms} ms before an earlier-listed event"
                )
                self._issue(Severity.WARNING, "source_order_inversion", message, event=event)
            latest_start = max(latest_start, event.start_ms)

    def _check_same_type_overlap(self, events: list[_RawEvent]) -> None:
        """INFO when two events of the same type overlap in time (e.g. two camera shots).
        Cross-type overlap (dialogue over a shot) is normal and not reported."""
        latest_end: dict[EventType, int] = {}
        for event in sorted(events, key=lambda e: (e.start_ms, e.source_index)):
            assert event.start_ms is not None and event.end_ms is not None
            kind = _classify(event.label)
            previous_end = latest_end.get(kind, -1)
            if event.start_ms < previous_end:
                overlap = min(previous_end, event.end_ms) - event.start_ms
                message = f"overlaps an earlier {kind.value} event by {overlap} ms"
                self._issue(Severity.INFO, "same_type_overlap", message, event=event)
            latest_end[kind] = max(previous_end, event.end_ms)

    # -- model building ----------------------------------------------------

    def _build(self, header: _Header, scenes: list[_RawScene], sha256: str) -> Episode | None:
        assert self.episode_number is not None
        filename = _FILENAME.match(PurePosixPath(self.source).name)
        try:
            return Episode(
                episode_number=self.episode_number,
                source_name=self.source,
                source_sha256=sha256,
                source_id=filename.group(2) if filename else None,
                logline=header.values.get("EPISODE LOGLINE") or None,
                continuity_notes=header.values.get("CONTINUITY NOTES") or None,
                extra_metadata=header.extra,
                scenes=tuple(self._build_scene(scene) for scene in scenes),
                unparsed_lines=tuple(self.unparsed),
            )
        except ValidationError as exc:
            # Report locations and error types only; pydantic messages can echo input text.
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in exc.errors()
            )
            self._issue(Severity.ERROR, "model_validation", details)
            return None

    def _build_scene(self, scene: _RawScene) -> Scene:
        assert self.episode_number is not None
        return Scene(
            episode_number=self.episode_number,
            scene_number=scene.number,
            heading=scene.fields["heading"],
            start_ms=scene.start_ms,
            end_ms=scene.end_ms,
            source_line=scene.line,
            events=tuple(self._build_event(event) for event in scene.events),
            unparsed_lines=tuple(scene.unparsed),
        )

    def _build_event(self, event: _RawEvent) -> ScriptEvent:
        assert self.episode_number is not None
        event_type = _classify(event.label)
        speaker, annotations = None, ()
        if event_type is EventType.DIALOGUE:
            speaker, annotations = _split_speaker(event.label) or (event.label, ())
        return ScriptEvent(
            event_id=self._event_id(event),
            episode_number=self.episode_number,
            scene_number=event.scene_number,
            source_index=event.source_index,
            source_line=event.line,
            event_type=event_type,
            label=event.label,
            speaker=speaker,
            annotations=annotations,
            text="\n".join(event.text),
            start_ms=event.start_ms,
            end_ms=event.end_ms,
        )


def _classify(label: str) -> EventType:
    """CAMERA / ACTION / SOUND FX are fixed labels; any other label is a dialogue speaker."""
    return _NON_DIALOGUE.get(" ".join(label.upper().split()), EventType.DIALOGUE)


def _split_speaker(label: str) -> tuple[str, tuple[str, ...]] | None:
    """Split ``NAME (O.S.) (Calm)`` into ``("NAME", ("O.S.", "Calm"))``.

    Returns None if the label has unbalanced or nested parentheses or no name.
    """
    match = _SPEAKER.fullmatch(label.strip())
    if match is None or not match.group("name").strip():
        return None
    notes = tuple(note.strip() for note in _NOTE.findall(match.group("notes")))
    return match.group("name").strip(), notes
