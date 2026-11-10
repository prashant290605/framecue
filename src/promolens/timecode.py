"""Exact conversion between screenplay timestamps and integer milliseconds.

Screenplay timestamps are ``MM:SS.cc`` (minutes, seconds, centiseconds). All timing logic in
FrameCue uses integer milliseconds so that durations and overlaps are exact; floats are never
involved.
"""

import re

# Minutes: two or more digits; seconds 00-59; exactly two centisecond digits.
_TIMESTAMP = re.compile(r"(\d{2,}):([0-5]\d)\.(\d{2})")


def parse_timestamp(value: str) -> int:
    """Convert ``MM:SS.cc`` to integer milliseconds. Raises ``ValueError`` if malformed."""
    match = _TIMESTAMP.fullmatch(value.strip())
    if match is None:
        raise ValueError("timestamp is not in MM:SS.cc format")
    minutes, seconds, centis = (int(group) for group in match.groups())
    return (minutes * 60 + seconds) * 1000 + centis * 10


def format_timestamp(ms: int) -> str:
    """Format milliseconds as ``MM:SS.cc``, or ``MM:SS.mmm`` if not a whole centisecond."""
    if ms < 0:
        raise ValueError("timestamp cannot be negative")
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    if millis % 10 == 0:
        return f"{minutes:02d}:{seconds:02d}.{millis // 10:02d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
