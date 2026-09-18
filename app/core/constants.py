"""Constants shared across modules (would otherwise be duplicated or owned by an unrelated module)."""

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"

CONTENT_DISPOSITION_ATTACHMENT = 'attachment; filename="{filename}"'

MEDIA_TYPE_XML = "application/xml"
MEDIA_TYPE_GEOJSON = "application/geo+json"

# Shared with the SQLAlchemy columns that store these values, so the two can't silently drift apart.
NAME_MIN_LENGTH = 1
NAME_MAX_LENGTH = 255
STORAGE_KEY_MAX_LENGTH = 1024
