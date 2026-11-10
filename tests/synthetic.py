"""Builders for synthetic screenplay text. All content is invented; nothing comes from the pack."""

from pathlib import Path

from promolens.parser import parse_episode_text
from promolens.schemas import Episode
from promolens.timecode import format_timestamp

FIXTURES = Path(__file__).parent / "fixtures"


def ts(ms: int) -> str:
    return format_timestamp(ms)


def ev(label: str, start_ms: int, end_ms: int, text: str = "A plain beat.") -> str:
    """Event block with millisecond times."""
    if label not in ("CAMERA", "ACTION", "SOUND FX") and not text.startswith('"'):
        text = f'"{text}"'
    return event(label, ts(start_ms), ts(end_ms), text)


def build(scenes: list[tuple[int, int, list[str]]], number: int = 1) -> Episode:
    """Parse an episode from ``(scene_start_ms, scene_end_ms, events)`` tuples."""
    blocks = [scene(i + 1, ts(s), ts(e), events) for i, (s, e, events) in enumerate(scenes)]
    result = parse_episode_text(episode(number, blocks), "synthetic.txt").raise_for_errors()
    return result.episodes[0]


def shots(start_ms: int, end_ms: int, step_ms: int) -> list[str]:
    """Contiguous camera shots covering ``[start_ms, end_ms)``."""
    times = [*range(start_ms, end_ms, step_ms), end_ms]
    return [ev("CAMERA", a, b, "A neutral shot.") for a, b in zip(times, times[1:], strict=False)]


def event(label: str, start: str, end: str, text: str = "A synthetic action happens.") -> str:
    return f"**{label}:**\nStart: ({start}) | End: ({end})\n{text}\n"


def scene(
    number: int,
    start: str,
    end: str,
    events: list[str],
    heading: str = "INT. TEST ROOM – DAY",
    upper: bool = False,
) -> str:
    keys = ["Scene heading", "Scene start", "Scene end", "Actions and dialogue"]
    if upper:
        keys = [key.upper() for key in keys]
    head = f"SCENE {number:02d}\n{keys[0]}: {heading}\n{keys[1]}: {start}\n{keys[2]}: {end}\n"
    return head + f"\n{keys[3]}:\n\n" + "\n".join(events)


def episode(
    number: int,
    scenes: list[str],
    logline: str = "A synthetic logline.",
    notes: str = "Synthetic continuity notes.",
) -> str:
    header = (
        f"EPISODE NUMBER: Episode {number}\n"
        f"EPISODE LOGLINE: {logline}\n"
        f"CONTINUITY NOTES: {notes}\n\n"
    )
    return header + "--- SCRIPT START ---\n\n" + "\n".join(scenes) + "\n--- SCRIPT END ---\n"


def simple_episode(number: int = 1, upper: bool = False) -> str:
    """Two contiguous scenes, one event of each type, no anomalies."""
    first = scene(
        1,
        "00:00.00",
        "00:10.00",
        [
            event("CAMERA", "00:00.00", "00:05.00"),
            event("ACTION", "00:01.00", "00:04.00"),
            event("SPEAKER ONE (Calm)", "00:02.00", "00:03.50", '"A synthetic line."'),
            event("CAMERA", "00:05.00", "00:10.00"),
        ],
        upper=upper,
    )
    second = scene(
        2,
        "00:10.00",
        "00:21.00",
        [
            event("CAMERA", "00:10.00", "00:21.00"),
            event("SOUND FX", "00:12.00", "00:13.00", "A synthetic thud."),
        ],
        heading="EXT. TEST YARD – NIGHT",
        upper=upper,
    )
    return episode(number, [first, second])
