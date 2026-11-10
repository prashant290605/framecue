import stat
import warnings
import zipfile

import pytest

from framecue import parser
from framecue.parser import parse_path, parse_zip
from framecue.schemas import Severity
from synthetic import simple_episode


def make_zip(path, entries: dict[str, str | bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def codes(result, severity=None) -> list[str]:
    return [i.code for i in result.issues if severity is None or i.severity is severity]


def test_zip_with_nested_directories(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(
        archive,
        {
            "Show/season/episode-002-Episode 900.txt": simple_episode(2),
            "Show/episode-001-Episode 899.txt": simple_episode(1),
        },
    )
    result = parse_zip(archive)
    assert result.ok and result.issues == ()
    assert [e.episode_number for e in result.episodes] == [1, 2]
    assert result.episodes[0].source_name == "Show/episode-001-Episode 899.txt"
    assert result.episodes[0].source_id == "899"
    assert not (tmp_path / "Show").exists()  # nothing extracted to disk


def test_directory_and_zip_give_same_episodes(tmp_path) -> None:
    folder = tmp_path / "pack"
    (folder / "nested").mkdir(parents=True)
    # write_bytes: write_text would translate newlines on Windows and change the hash.
    (folder / "nested" / "a.txt").write_bytes(simple_episode(1).encode("utf-8"))
    (folder / "b.txt").write_bytes(simple_episode(2).encode("utf-8"))
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"nested/a.txt": simple_episode(1), "b.txt": simple_episode(2)})
    from_dir, from_zip = parse_path(folder), parse_path(archive)
    assert from_dir.ok and from_zip.ok
    assert from_dir.episodes == from_zip.episodes


def test_duplicate_episode_numbers_are_errors(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"a.txt": simple_episode(1), "b.txt": simple_episode(1)})
    result = parse_zip(archive)
    assert not result.ok
    assert codes(result, Severity.ERROR) == ["duplicate_episode_number"] * 2
    assert result.episodes == ()


def test_episode_sequence_gap_is_warning(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"a.txt": simple_episode(1), "c.txt": simple_episode(3)})
    result = parse_zip(archive)
    assert result.ok and codes(result) == ["episode_sequence_gap"]


def test_filename_header_mismatch_is_warning(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"episode-005-Episode 1.txt": simple_episode(4)})
    result = parse_zip(archive)
    assert result.ok and codes(result) == ["filename_episode_mismatch"]


@pytest.mark.parametrize(
    "name", ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "C:/evil.txt", "a\\..\\evil.txt"]
)
def test_unsafe_paths_are_rejected(tmp_path, name: str) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(archive, {name: simple_episode(1), "ok.txt": simple_episode(2)})
    result = parse_zip(archive)
    assert not result.ok
    assert codes(result, Severity.ERROR) == ["unsafe_archive_entry"]
    assert [e.episode_number for e in result.episodes] == [2]


def test_symlink_entry_is_rejected(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    result = parse_zip(archive)
    assert codes(result, Severity.ERROR) == ["unsafe_archive_entry"]
    assert "symbolic link" in result.issues[0].message


def test_encrypted_entry_is_rejected(tmp_path) -> None:
    # zipfile's writer clears the encryption flag, so set bit 0 in the raw headers instead.
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"secret.txt": simple_episode(1)})
    raw = bytearray(archive.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        raw[raw.index(signature) + flag_offset] |= 0x1
    archive.write_bytes(bytes(raw))
    assert zipfile.ZipFile(archive).infolist()[0].flag_bits & 0x1
    result = parse_zip(archive)
    assert codes(result, Severity.ERROR) == ["unsafe_archive_entry"]
    assert "encrypted" in result.issues[0].message


def test_duplicate_entry_names_are_rejected(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        zf.writestr("a.txt", simple_episode(1))
        zf.writestr("a.txt", simple_episode(2))
    result = parse_zip(archive)
    assert codes(result, Severity.ERROR) == ["unsafe_archive_entry"] * 2
    assert result.episodes == ()


def test_oversized_entry_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_ENTRY_BYTES", 100)
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"big.txt": simple_episode(1)})
    result = parse_zip(archive)
    assert codes(result, Severity.ERROR) == ["unsafe_archive_entry"]


def test_too_many_entries_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_ARCHIVE_ENTRIES", 1)
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"a.txt": simple_episode(1), "b.txt": simple_episode(2)})
    assert codes(parse_zip(archive), Severity.ERROR) == ["too_many_entries"]


def test_metadata_and_non_text_entries_are_skipped(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    make_zip(
        archive,
        {
            "__MACOSX/._a.txt": b"\x00\x05\x16\x07",
            "._b.txt": b"\x00",
            "notes.pdf": b"%PDF",
            "a.txt": simple_episode(1),
        },
    )
    result = parse_zip(archive)
    assert result.ok and len(result.episodes) == 1
    assert codes(result, Severity.INFO) == ["skipped_entry"] * 3


def test_bad_or_empty_archives(tmp_path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    assert codes(parse_zip(junk)) == ["unreadable_archive"]
    empty = tmp_path / "empty.zip"
    make_zip(empty, {})
    assert codes(parse_zip(empty)) == ["no_episodes"]


def test_errors_in_one_file_do_not_hide_others(tmp_path) -> None:
    archive = tmp_path / "pack.zip"
    broken = simple_episode(2).replace("00:05.00", "00:5.00", 1)
    make_zip(archive, {"a.txt": simple_episode(1), "b.txt": broken})
    result = parse_zip(archive)
    assert not result.ok
    assert [e.episode_number for e in result.episodes] == [1]
    assert {i.source for i in result.errors} == {"b.txt"}
