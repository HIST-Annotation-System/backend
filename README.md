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
   - `minio` - object storage (API `http://localhost:9000`, Web UI `http://localhost:9001`).

4. **Stop the environment:**
   ```bash
   docker compose down
   ```

### Running outside Docker

```bash
docker compose up -d db minio      # infrastructure only
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

## Slides

### Supported formats

Single-file formats (`.svs`, `.tif`, `.tiff`, `.ndpi`, `.scn`, `.bif`, `.svslide`) and plain images
(`.jpg`, `.jpeg`, `.png`, `.bmp`) are uploaded as-is.

**MRXS (MIRAX) slides consist of a `.mrxs` file and a data folder with the same name.** Upload them as a single
`.zip` archive containing both:

```
CMU-1.zip
├─ CMU-1.mrxs
└─ CMU-1/
   ├─ Slidedat.ini
   ├─ Index.dat
   └─ Data0000.dat ...
```

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
| `GET /slides` | Slide library, newest first |
| `GET /slides/{id}` | Slide metadata, OpenSlide properties, tissue box |
| `GET /slides/{id}/image.dzi` | Deep Zoom descriptor (OpenSeadragon tile source) |
| `GET /slides/{id}/image_files/{level}/{col}_{row}.jpeg` | Deep Zoom tile |
| `GET /slides/{id}/thumbnail` | Thumbnail (JPEG) |
| `GET /slides/{id}/region` | Export a level-0 region as PNG (longer side at most 2048 px) |
| `GET /annotation-classes` | Tissue classes used to label annotations |
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
| `DATABASE_URL` | PostgreSQL connection (`postgresql+psycopg://...`) |
| `S3_ENDPOINT` / `S3_PUBLIC_ENDPOINT` | Object storage endpoint used by the backend / by browsers for presigned URLs |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME` | Object storage credentials and bucket |
| `SLIDE_CACHE_DIR` | Directory holding local copies of slides |

Only deployment-specific values are settings. Fixed parameters (Deep Zoom tile size, thumbnail size, tissue
detection thresholds, limits, error messages) are UPPER_CASE constants at the top of the module that uses them.

To add new variables, add them to the `Settings` class in `app/core/config.py`, and then set their values in your `.env.local` file.
