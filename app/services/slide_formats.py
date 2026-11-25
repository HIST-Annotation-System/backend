"""The slide formats the application accepts, and the name/layout rules that enforce them.

Only formats the bundled OpenSlide reads natively are listed. Single-file formats are accepted only as
single files; multi-file formats only as one .zip archive (or an extracted folder imported from storage).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.exceptions.errors import InvalidInputError


@dataclass(frozen=True)
class SlideFormat:
    name: str
    # The entry file OpenSlide opens.
    suffix: str
    # Accepted `openslide.OpenSlide.detect_format()` results for the entry file.
    vendors: frozenset[str]
    # Suffixes of the files that accompany the entry file; empty for single-file formats.
    companion_suffixes: frozenset[str] = frozenset()


TIFF_VENDORS = frozenset({"aperio", "philips", "ventana", "generic-tiff"})

SLIDE_FORMATS: tuple[SlideFormat, ...] = (
    SlideFormat("Aperio SVS", ".svs", frozenset({"aperio"})),
    SlideFormat("TIFF", ".tif", TIFF_VENDORS),
    SlideFormat("TIFF", ".tiff", TIFF_VENDORS),
    SlideFormat("Hamamatsu NDPI", ".ndpi", frozenset({"hamamatsu"})),
    SlideFormat("Leica SCN", ".scn", frozenset({"leica"})),
    SlideFormat("Ventana BIF", ".bif", frozenset({"ventana"})),
    SlideFormat("Sakura SVSlide", ".svslide", frozenset({"sakura"})),
    SlideFormat("Zeiss CZI", ".czi", frozenset({"zeiss"})),
    SlideFormat("MIRAX MRXS", ".mrxs", frozenset({"mirax"}), frozenset({".ini", ".dat"})),
    SlideFormat("Hamamatsu VMS", ".vms", frozenset({"hamamatsu"}), frozenset({".jpg", ".opt"})),
    SlideFormat("Hamamatsu VMU", ".vmu", frozenset({"hamamatsu"}), frozenset({".ngr"})),
    SlideFormat("DICOM WSI", ".dcm", frozenset({"dicom"}), frozenset({".dcm"})),
)
FORMATS_BY_SUFFIX = {slide_format.suffix: slide_format for slide_format in SLIDE_FORMATS}
SINGLE_FILE_SUFFIXES = frozenset(f.suffix for f in SLIDE_FORMATS if not f.companion_suffixes)
MULTI_FILE_SUFFIXES = frozenset(f.suffix for f in SLIDE_FORMATS if f.companion_suffixes)
# Multi-file formats opened through one index file (any file of a DICOM series opens it).
INDEX_SUFFIXES = frozenset({".mrxs", ".vms", ".vmu"})
DICOM_SUFFIX = ".dcm"
MRXS_SUFFIX = ".mrxs"
MRXS_SLIDEDAT = "slidedat.ini"
ARCHIVE_SUFFIX = ".zip"
# Larger slides without a resolution pyramid can't be viewed or analysed efficiently.
PYRAMID_REQUIRED_ABOVE_PX = 10_000
# Metadata that archiving tools add; skipped when validating and extracting.
IGNORED_DIRECTORY_NAMES = frozenset({"__MACOSX"})
IGNORED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})

UNSUPPORTED_FILE_TYPE = "Unsupported file type '{suffix}'. Supported: {supported}."
MULTI_FILE_NEEDS_ARCHIVE = (
    "{format} is a multi-file format - upload the slide and its companion files as one .zip archive."
)
SINGLE_FILE_IN_ARCHIVE = "Single-file formats must be uploaded directly, not inside an archive ('{name}')."
AMBIGUOUS_ARCHIVE = "The archive must contain exactly one slide."
NO_SLIDE_IN_ARCHIVE = "The archive doesn't contain a supported multi-file slide."
MRXS_MISSING_SLIDEDAT = "MRXS data folder '{folder}/' is missing Slidedat.ini."
UNEXPECTED_ARCHIVE_MEMBER = "Unexpected file '{name}' in the archive."


def validate_upload_name(filename: str) -> tuple[str, str]:
    """(stem, lowercase suffix) of an uploadable file name: a single-file slide or a .zip archive."""
    path = PurePosixPath(filename.replace("\\", "/"))
    suffix = path.suffix.lower()
    if suffix in SINGLE_FILE_SUFFIXES or suffix == ARCHIVE_SUFFIX:
        return path.stem, suffix
    if suffix in MULTI_FILE_SUFFIXES:
        raise InvalidInputError(MULTI_FILE_NEEDS_ARCHIVE.format(format=FORMATS_BY_SUFFIX[suffix].name))
    supported = f"{', '.join(sorted(SINGLE_FILE_SUFFIXES))}; {', '.join(sorted(MULTI_FILE_SUFFIXES))} as a .zip"
    raise InvalidInputError(UNSUPPORTED_FILE_TYPE.format(suffix=suffix or path.name, supported=supported))


def is_ignored_member(path: PurePosixPath) -> bool:
    return path.name in IGNORED_FILE_NAMES or any(part in IGNORED_DIRECTORY_NAMES for part in path.parts)


def validate_slide_members(paths: Sequence[PurePosixPath]) -> SlideFormat:
    """Check that the files of an archive or folder (paths relative to its root) form exactly one multi-file slide."""
    members = [path for path in paths if not is_ignored_member(path)]
    for path in members:
        suffix = path.suffix.lower()
        if suffix in SINGLE_FILE_SUFFIXES or suffix == ARCHIVE_SUFFIX:
            raise InvalidInputError(SINGLE_FILE_IN_ARCHIVE.format(name=path))

    entries = [path for path in members if path.suffix.lower() in INDEX_SUFFIXES]
    if len(entries) > 1:
        raise InvalidInputError(AMBIGUOUS_ARCHIVE)
    if entries:
        return _validate_indexed_slide(entries[0], members)
    for path in members:
        if path.suffix.lower() != DICOM_SUFFIX:
            raise InvalidInputError(UNEXPECTED_ARCHIVE_MEMBER.format(name=path))
    if not members:
        raise InvalidInputError(NO_SLIDE_IN_ARCHIVE)
    if len({path.parent for path in members}) > 1:
        raise InvalidInputError(AMBIGUOUS_ARCHIVE)
    return FORMATS_BY_SUFFIX[DICOM_SUFFIX]


def _validate_indexed_slide(entry: PurePosixPath, members: Sequence[PurePosixPath]) -> SlideFormat:
    slide_format = FORMATS_BY_SUFFIX[entry.suffix.lower()]
    # MRXS keeps its companions in a data folder named after the index file; VMS/VMU next to it.
    companion_dir = entry.parent / entry.stem if slide_format.suffix == MRXS_SUFFIX else entry.parent
    companions = [path for path in members if path != entry]
    for path in companions:
        if path.parent != companion_dir or path.suffix.lower() not in slide_format.companion_suffixes:
            raise InvalidInputError(UNEXPECTED_ARCHIVE_MEMBER.format(name=path))
    if slide_format.suffix == MRXS_SUFFIX and not any(path.name.lower() == MRXS_SLIDEDAT for path in companions):
        raise InvalidInputError(MRXS_MISSING_SLIDEDAT.format(folder=companion_dir))
    return slide_format
