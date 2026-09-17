# Backend API (FastAPI)

Backend for the Whole Slide Image (WSI) viewer, built on **FastAPI**. It stores slides in object storage
(MinIO/S3), keeps slide metadata and annotations in **PostgreSQL**, and serves Deep Zoom tiles with **OpenSlide**.
Dependency and environment management is handled by **uv**.

## Directory Structure

The main application code is located in the `app/` folder:

- `core/` - Application configuration (Pydantic settings).
- `dependencies/` - Dependency injection: process-wide resources created at startup (`resources.py`) and request-scoped providers.
- `exceptions/` - Application errors and the handler that maps them to HTTP responses (`{"detail": ...}` bodies).
- `models/` - SQLAlchemy models (`Slide`, `AnnotationClass`, `Annotation`).
- `repositories/` - Data access layer.
- `routers/` - API endpoint definitions grouped functionally (no business logic).
- `schemas/` - Pydantic models (DTOs) used for input and output data validation.
- `services/` - Business logic: uploads, ingestion, Deep Zoom tiling, annotations, object storage.

Database migrations live in `alembic/`.

### Statelessness

Every endpoint addresses slides by id (`/slides/{slide_id}/...`); there is no "current slide". Infrastructure
(DB engine, S3 clients, the cache of opened slides) is created once in the FastAPI lifespan and injected through
dependencies. Opened OpenSlide handles are cached in a bounded LRU keyed by the immutable slide id - a pure
performance cache that any worker may rebuild at any time.

## Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- For running outside Docker: [uv](https://docs.astral.sh/uv/) (OpenSlide binaries come from the `openslide-bin` wheel)

## Installation and Local Setup

1. **Clone the repository** and navigate to the backend folder.
2. **Create a local environment file:**
   Copy `.env.example` to `.env.local` (the file read by `app/core/config.py`) and adjust the variables if necessary.
3. **Run the environment using Docker Compose:**
   ```bash
   docker compose up -d --build
   ```
   This starts:
   - `db` - PostgreSQL 17 (published on host port **5433** to avoid clashing with a local PostgreSQL),
   - `migrate` - a one-shot container running `alembic upgrade head`,
   - `api` - the FastAPI server at `http://localhost:8000`,
   - `minio` - object storage (API `http://localhost:9000`, Web UI `http://localhost:9001`),
   - `loki` / `promtail` / `grafana` - log aggregation (see [Logs](#logs) below).

4. **Stop the environment:**
   ```bash
   docker compose down
   ```

### Running outside Docker

```bash
docker compose up -d db minio      # infrastructure only
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --no-access-log
```

## Slides

### Supported formats

Only formats the bundled OpenSlide reads natively are accepted (`app/services/slide_formats.py`), and every slide
needs resolution metadata (MPP) and, above 10,000 px, a resolution pyramid. Anything else is rejected: by name
when the upload starts, by content during ingestion (the slide ends `failed` with the reason).

| Kind | Formats | Upload as |
|---|---|---|
| Single file | Aperio `.svs`, pyramidal tiled `.tif`/`.tiff`, Hamamatsu `.ndpi`, Leica `.scn`, Ventana `.bif`, Sakura `.svslide`, Zeiss `.czi` | The file itself, never inside an archive |
| Multi-file | MIRAX `.mrxs`, Hamamatsu `.vms`/`.vmu`, DICOM WSI `.dcm` | One `.zip` with exactly one slide and nothing else |

Archive layouts (`__MACOSX/`, `.DS_Store` and `Thumbs.db` are ignored):

```
CMU-1.zip                slide.zip                series.zip
├─ CMU-1.mrxs            ├─ slide.vms             └─ series/
└─ CMU-1/                ├─ slide.opt                ├─ level-0.dcm
   ├─ Slidedat.ini       └─ slide_*.jpg              └─ level-1.dcm ...
   └─ *.dat              (.vmu: index + .ngr files in one folder; DICOM files need the .dcm extension)
```

Not supported: plain images (`.jpg`, `.png`, `.bmp`, ...), flat or OME-TIFF, stripped TIFF, Trestle, Olympus
`.vsi`, Philips iSyntax and nested archives. Convert them to a pyramidal tiled TIFF with resolution first, e.g.
with QuPath or `vips tiffsave in.tif out.tif --tile --pyramid --compression jpeg`.

### Ingestion

After an upload completes, a background job copies the slide into the local slide cache (`SLIDE_CACHE_DIR`,
extracting archives safely), validates it with OpenSlide, and stores its metadata, a thumbnail, and the bounding
box of the detected tissue. The slide status goes `uploading` → `processing` → `ready` (or `failed` with an
`error_message`).

Slides are added through the frontend's upload dialog (e.g. `examples/CMU-2.svs`).

## API Overview

All endpoints are prefixed with `/api/v1`.

| Method & path | Purpose |
|---|---|
| `POST /upload/start` | Create a slide (`uploading`) and start a multipart upload |
| `POST /upload/presigned-urls`, `POST /upload/refresh-urls` | Presigned part URLs for a slide that is uploading |
| `POST /upload/complete` | Finish the upload and start ingestion |
| `POST /upload/import-from-storage` | Register an object already in the bucket (a key ending in `/` imports an extracted multi-file slide folder; keys under `slides/` or overlapping another slide are rejected) |
| `GET /slides` | Slide library, newest first |
| `GET /slides/{id}` | Slide metadata, OpenSlide properties, tissue box |
| `GET /slides/{id}/image.dzi` | Deep Zoom descriptor (OpenSeadragon tile source) |
| `GET /slides/{id}/image_files/{level}/{col}_{row}.jpeg` | Deep Zoom tile |
| `GET /slides/{id}/thumbnail` | Thumbnail (JPEG) |
| `GET /slides/{id}/region` | Export a level-0 region as PNG (longer side at most 2048 px) |
| `GET /annotation-classes` | Tissue classes (tumour, stroma) used to label annotations; unannotated tissue is negative |
| `GET, POST /slides/{id}/annotations` | List / create annotations (GeoJSON polygons, level-0 pixels) |
| `PATCH, DELETE /slides/{id}/annotations/{annotation_id}` | Update / delete an annotation |
| `GET /slides/{id}/annotations/export` | Download annotations as GeoJSON (QuPath-style properties) |

Once the server is running, the interactive Swagger UI documentation is available at:
- [http://localhost:8000/docs](http://localhost:8000/docs)

Alternative documentation (ReDoc):
- [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Tests and type checks

```bash
uv run pytest            # unit + API tests (SQLite, local fake storage, synthetic slides)
uv run pytest -m slow    # integration test with examples/CMU-2.svs
uv run mypy app          # strict type checking
uv run mypy --explicit-package-bases tests
```

## Logs

The app logs one JSON line per event to stdout (see `app/core/logging.py`): every request gets a
`request completed` (or `request failed`) line with method, path, status code and duration, tagged
with a `request_id` that's also echoed back as the `X-Request-ID` response header, so all log lines
for one request can be correlated. Set `LOG_LEVEL=DEBUG` in `.env.local` for more verbose output
while diagnosing an issue.

`docker compose up` also starts a small aggregation stack so you don't have to `docker compose logs`
by hand:

- **Promtail** tails every container's Docker logs (via the Docker socket) and ships them to Loki.
- **Loki** stores and indexes them (by `service` and `level`).
- **Grafana** (`http://localhost:3001`, no login required) has Loki pre-configured as a datasource,
  with a starter **"Application Logs"** dashboard (log volume by level, an errors/warnings feed, and
  the full `api` log stream). Use **Explore** for ad-hoc [LogQL](https://grafana.com/docs/loki/latest/query/)
  queries, e.g. `{service="api"} | json | status_code >= 500` or `{service="api"} |= "<request-id>"`
  to pull every line for one request.

This stack is meant for local/dev use (anonymous admin access, filesystem storage) - lock it down
(auth, retention, real storage) before running it anywhere it'd be exposed.

## Database migrations

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe change" --rev-id 0003_describe_change
uv run alembic check     # fails if models and migrations have drifted
```

## Environment Variables

Application configuration is loaded using `pydantic-settings`.
Loading priorities:
1. Environment variables passed via `docker-compose.yml` or system terminal
2. `.env.local` file

The main variables are listed in `.env.example`:

| Variable | Purpose |
|---|---|
| `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL connection; `DATABASE_URL` is built from these (see `app/core/config.py`). Default server/port (`localhost`/`5433`) target the host-published port; docker-compose overrides them to `db`/`5432` for the api/migrate containers |
| `POSTGRES_HOST_PORT` | Host port Postgres is published on (compose only, defaults to `5433`) |
| `BUCKET_ENDPOINT` / `BUCKET_PUBLIC_ENDPOINT` | Object storage endpoint used by the backend / by browsers for presigned URLs |
| `BUCKET_ACCESS_KEY`, `BUCKET_SECRET_KEY`, `BUCKET_NAME` | Object storage credentials and bucket |
| `SLIDE_CACHE_DIR` | Directory holding local copies of slides |

`docker-compose.yml` loads every service's config straight from `.env.local` via `env_file:`, so
`docker compose up` needs no `--env-file` flag and can't silently see blank credentials the way
shell-style `${VAR}` interpolation could.

Only deployment-specific values are settings. Fixed parameters (Deep Zoom tile size, thumbnail size, tissue
detection thresholds, limits, error messages) are UPPER_CASE constants at the top of the module that uses them.

To add new variables, add them to the `Settings` class in `app/core/config.py`, and then set their values in your `.env.local` file.
