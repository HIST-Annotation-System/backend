import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from app.exceptions.errors import InvalidInputError
from app.services import slide_files
from app.services.slide_files import extract_zip_safely, find_slide_entry, list_folder_members
from app.services.storage import StorageObject

MRXS = {"s.mrxs": b"index", "s/Slidedat.ini": b"[GENERAL]", "s/Data0000.dat": b"\x00" * 64}


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


class TestExtractZipSafely:
    def test_extracts_a_valid_slide(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", MRXS)
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert (out / "s.mrxs").read_bytes() == b"index"
        assert (out / "s" / "Data0000.dat").stat().st_size == 64

    def test_leaves_out_archive_metadata(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {**MRXS, "__MACOSX/s/._x": b"meta", ".DS_Store": b"meta"})
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert not (out / "__MACOSX").exists()
        assert not (out / ".DS_Store").exists()

    def test_backslash_separators_become_folders(self, tmp_path: Path) -> None:
        members = {"s.mrxs": b"i", "s\\Slidedat.ini": b"x", "s\\Data0000.dat": b"d"}
        archive = make_zip(tmp_path / "a.zip", members)
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert (out / "s" / "Slidedat.ini").is_file()

    def test_path_traversal_is_rejected_and_nothing_escapes(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"../../evil.dcm": b"x", "../../evil2.dcm": b"y"})
        out = tmp_path / "deep" / "out"
        # Validation sees both in one folder; the traversal check must refuse them.
        with pytest.raises(InvalidInputError, match="unsafe path"):
            extract_zip_safely(archive, out)
        assert not (tmp_path / "evil.dcm").exists()

    def test_oversized_content_is_rejected_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slide_files, "ZIP_MAX_UNCOMPRESSED_BYTES", 10)
        archive = make_zip(tmp_path / "a.zip", MRXS)
        out = tmp_path / "out"
        with pytest.raises(InvalidInputError, match="too large"):
            extract_zip_safely(archive, out)
        assert not out.exists()

    def test_not_a_zip(self, tmp_path: Path) -> None:
        bogus = tmp_path / "a.zip"
        bogus.write_bytes(b"definitely not a zip")
        with pytest.raises(InvalidInputError, match="not a valid ZIP"):
            extract_zip_safely(bogus, tmp_path / "out")

    def test_archive_that_is_not_one_slide(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"inner.svs": b"x"})
        with pytest.raises(InvalidInputError, match="uploaded directly"):
            extract_zip_safely(archive, tmp_path / "out")


class TestFindSlideEntry:
    def write(self, root: Path, *names: str) -> None:
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def test_single_file(self, tmp_path: Path) -> None:
        self.write(tmp_path, "slide.svs")
        assert find_slide_entry(tmp_path) == tmp_path / "slide.svs"

    def test_index_file_of_a_multi_file_slide(self, tmp_path: Path) -> None:
        self.write(tmp_path, "s.mrxs", "s/Slidedat.ini", "s/Data0000.dat")
        assert find_slide_entry(tmp_path) == tmp_path / "s.mrxs"

    def test_any_file_of_a_dicom_series(self, tmp_path: Path) -> None:
        self.write(tmp_path, "b.dcm", "a.dcm")
        assert find_slide_entry(tmp_path) == tmp_path / "a.dcm"

    @pytest.mark.parametrize("names", [(), ("a.txt", "b.txt"), ("a.mrxs", "b.vms")])
    def test_ambiguous_or_empty(self, tmp_path: Path, names: tuple[str, ...]) -> None:
        self.write(tmp_path, *names)
        with pytest.raises(InvalidInputError):
            find_slide_entry(tmp_path)


class FakeStorage:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def iter_keys(self, prefix: str) -> Iterator[StorageObject]:
        for key in self.keys:
            if key.startswith(prefix):
                yield StorageObject(key=key, size=1, last_modified=datetime(2026, 1, 1, tzinfo=UTC))


def test_list_folder_members_skips_folder_markers_and_metadata() -> None:
    storage = FakeStorage(["imp/x/", "imp/x/s.mrxs", "imp/x/s/", "imp/x/s/Slidedat.ini", "imp/x/.DS_Store", "other/y"])
    members = list_folder_members(storage, "imp/x/")  # type: ignore[arg-type]
    assert set(members) == {PurePosixPath("s.mrxs"), PurePosixPath("s/Slidedat.ini")}
    assert members[PurePosixPath("s.mrxs")].key == "imp/x/s.mrxs"
