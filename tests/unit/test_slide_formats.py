from pathlib import PurePosixPath

import pytest

from app.exceptions.errors import InvalidInputError
from app.services.slide_formats import (
    FORMATS_BY_SUFFIX,
    is_ignored_member,
    validate_slide_members,
    validate_upload_name,
)


def paths(*names: str) -> list[PurePosixPath]:
    return [PurePosixPath(name) for name in names]


class TestValidateUploadName:
    @pytest.mark.parametrize("name", ["a.svs", "a.tif", "a.tiff", "a.ndpi", "a.scn", "a.bif", "a.svslide", "a.czi"])
    def test_accepts_single_file_formats(self, name: str) -> None:
        stem, suffix = validate_upload_name(name)
        assert stem == "a"
        assert suffix == name[1:]

    def test_accepts_zip_archives(self) -> None:
        assert validate_upload_name("slide.zip") == ("slide", ".zip")

    def test_suffix_is_case_insensitive_and_lowercased(self) -> None:
        assert validate_upload_name("Biopsy 01.SVS") == ("Biopsy 01", ".svs")

    def test_windows_paths_keep_only_the_file_name(self) -> None:
        assert validate_upload_name(r"C:\scans\case.ndpi") == ("case", ".ndpi")

    @pytest.mark.parametrize(("name", "format_name"), [("a.mrxs", "MIRAX"), ("a.vms", "VMS"), ("a.dcm", "DICOM")])
    def test_multi_file_formats_ask_for_an_archive(self, name: str, format_name: str) -> None:
        with pytest.raises(InvalidInputError, match=f"{format_name}.*zip archive"):
            validate_upload_name(name)

    def test_unknown_suffix_is_rejected_with_the_supported_list(self) -> None:
        with pytest.raises(InvalidInputError, match=r"Unsupported file type '\.png'.*\.svs"):
            validate_upload_name("photo.png")

    def test_name_without_suffix_reports_the_name(self) -> None:
        with pytest.raises(InvalidInputError, match="Unsupported file type 'README'"):
            validate_upload_name("README")


class TestIsIgnoredMember:
    @pytest.mark.parametrize("name", [".DS_Store", "dir/Thumbs.db", "__MACOSX/a.mrxs", "x/__MACOSX/y/z.dat"])
    def test_archive_metadata_is_ignored(self, name: str) -> None:
        assert is_ignored_member(PurePosixPath(name))

    @pytest.mark.parametrize("name", ["a.mrxs", "a/Slidedat.ini", "MACOSX/a.dat"])
    def test_real_files_are_kept(self, name: str) -> None:
        assert not is_ignored_member(PurePosixPath(name))


class TestValidateSlideMembers:
    def test_complete_mrxs_slide(self) -> None:
        members = paths("s.mrxs", "s/Slidedat.ini", "s/Data0000.dat", "s/Data0001.dat")
        assert validate_slide_members(members) is FORMATS_BY_SUFFIX[".mrxs"]

    def test_mrxs_inside_a_folder_of_the_archive(self) -> None:
        members = paths("root/s.mrxs", "root/s/slidedat.INI", "root/s/Data0000.dat")
        assert validate_slide_members(members).name == "MIRAX MRXS"

    def test_mrxs_without_slidedat_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="missing Slidedat.ini"):
            validate_slide_members(paths("s.mrxs", "s/Data0000.dat"))

    def test_mrxs_companion_outside_its_data_folder_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="Unexpected file 'Data0000.dat'"):
            validate_slide_members(paths("s.mrxs", "s/Slidedat.ini", "Data0000.dat"))

    def test_companion_with_a_foreign_suffix_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="Unexpected file 's/notes.txt'"):
            validate_slide_members(paths("s.mrxs", "s/Slidedat.ini", "s/notes.txt"))

    def test_vms_companions_sit_next_to_the_index(self) -> None:
        members = paths("v.vms", "v_1.jpg", "v.opt")
        assert validate_slide_members(members) is FORMATS_BY_SUFFIX[".vms"]

    def test_vmu_with_its_ngr(self) -> None:
        assert validate_slide_members(paths("u.vmu", "u.ngr")) is FORMATS_BY_SUFFIX[".vmu"]

    def test_two_index_files_are_ambiguous(self) -> None:
        with pytest.raises(InvalidInputError, match="exactly one slide"):
            validate_slide_members(paths("a.mrxs", "a/Slidedat.ini", "b.vms"))

    def test_dicom_series_in_one_folder(self) -> None:
        assert validate_slide_members(paths("d/1.dcm", "d/2.DCM", "d/3.dcm")) is FORMATS_BY_SUFFIX[".dcm"]

    def test_dicom_files_spread_over_folders_are_ambiguous(self) -> None:
        with pytest.raises(InvalidInputError, match="exactly one slide"):
            validate_slide_members(paths("a/1.dcm", "b/2.dcm"))

    def test_dicom_with_a_stray_file_is_rejected(self) -> None:
        with pytest.raises(InvalidInputError, match="Unexpected file 'readme.txt'"):
            validate_slide_members(paths("1.dcm", "readme.txt"))

    @pytest.mark.parametrize("inner", ["inner.svs", "nested.zip", "x/scan.TIFF"])
    def test_single_file_slides_and_nested_archives_are_rejected(self, inner: str) -> None:
        with pytest.raises(InvalidInputError, match="uploaded directly"):
            validate_slide_members(paths("s.mrxs", "s/Slidedat.ini", inner))

    def test_empty_archive_has_no_slide(self) -> None:
        with pytest.raises(InvalidInputError, match="doesn't contain"):
            validate_slide_members([])

    def test_archive_of_only_metadata_has_no_slide(self) -> None:
        with pytest.raises(InvalidInputError, match="doesn't contain"):
            validate_slide_members(paths("__MACOSX/._s.mrxs", ".DS_Store"))

    def test_metadata_next_to_a_valid_slide_is_ignored(self) -> None:
        members = paths("s.mrxs", "s/Slidedat.ini", "__MACOSX/s/._Slidedat.ini", "s/.DS_Store")
        assert validate_slide_members(members).suffix == ".mrxs"
