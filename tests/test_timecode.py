import pytest

from framecue.timecode import format_timestamp, parse_timestamp


@pytest.mark.parametrize(
    ("text", "expected_ms"),
    [
        ("00:00.00", 0),
        ("00:00.01", 10),
        ("00:04.80", 4_800),
        ("00:08.75", 8_750),
        ("00:59.99", 59_990),
        ("01:00.00", 60_000),
        ("02:18.00", 138_000),
        ("03:20.75", 200_750),
        ("120:00.00", 7_200_000),
        (" 00:01.50 ", 1_500),
    ],
)
def test_parse_timestamp_is_exact(text: str, expected_ms: int) -> None:
    assert parse_timestamp(text) == expected_ms


@pytest.mark.parametrize(
    "text",
    ["", "0:01.00", "00:1.00", "00:60.00", "00:01.0", "00:01.000", "00:01,50", "-00:01.00",
     "aa:bb.cc", "00:01", "00:01.5O", "(00:01.00)"],
)  # fmt: skip
def test_parse_timestamp_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamp(text)


def test_every_centisecond_round_trips_exactly() -> None:
    for centis in range(0, 4 * 60 * 100):
        ms = centis * 10
        text = format_timestamp(ms)
        assert parse_timestamp(text) == ms
        assert len(text) == 8


def test_format_non_centisecond_milliseconds() -> None:
    assert format_timestamp(1_234) == "00:01.234"
    with pytest.raises(ValueError):
        format_timestamp(-1)
