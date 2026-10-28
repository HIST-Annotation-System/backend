# Backend API (FastAPI)

Backend for the Whole Slide Image (WSI) viewer, built on **FastAPI**. It stores slides in **AWS S3**, keeps
slide metadata and annotations in **PostgreSQL** (RDS), and serves Deep Zoom
tiles with **OpenSlide**.
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

Database migrations live in `alembic/`, and the Terraform config for the AWS resources in `infra/`.

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
2. **Provision the AWS resources**, the RDS database and the S3 bucket, by following
   [`infra/README.md`](infra/README.md). Compose runs the FastAPI service and nothing else - there is no
   database container, so the stack has to exist first.
3. **Create a local environment file:**
   Copy `.env.example` to `.env.local` (the file read by `app/core/config.py`) and fill in the `BUCKET_*` and
   `POSTGRES_*` values from `terraform output -raw env_local`. Start the database tunnel
   (`terraform output -raw tunnel_command`, see [Working as a team](#working-as-a-team)) and keep it running. Without a reachable bucket the API still starts
   but uploads and slide viewing fail, and the log shows which bucket could not be reached; without a reachable
   database it does not start at all.
4. **Run the environment using Docker Compose:**
   ```bash
   docker compose up -d --build
   ```
   This starts:
   - `migrate` - a one-shot container running `alembic upgrade head` against RDS,
   - `api` - the FastAPI server at `http://localhost:8000` (with `--reload` on the mounted source).

   The `ADMIN_USERNAME` account already exists in RDS. On a fresh database, create it once with
   `docker compose run --rm api python -m scripts.create_admin`.

   The containers reach RDS through the tunnel on the host: compose overrides `POSTGRES_SERVER` with
   `host.docker.internal`, so `.env.local` keeps `localhost` for running outside Docker.

5. **Stop the environment:**
   ```bash
   docker compose down
   ```

### Running outside Docker

Same `.env.local`, no containers:

```bash
uv sync
uv run alembic upgrade head
uv run python -m scripts.create_admin     # once: creates the admin account
uv run uvicorn app.main:app --reload --no-access-log
```

## Infrastructure (AWS)

`infra/` holds the Terraform config for the cloud resources the backend runs on: an RDS PostgreSQL 17 instance,
the S3 bucket for slides, and an IAM user whose access keys the app uses. It is not optional any more - compose
has no database service, so the stack has to be applied before the backend can start.

One thing to know before starting: the region is pinned to `eu-north-1` by an organization SCP that denies every
other one, so `aws_region` cannot be moved closer to home.

**[`infra/README.md`](infra/README.md) is the deployment guide**: prerequisites, variables, apply, wiring up
`.env.local`, verification, teardown and troubleshooting.

## Working as a team

One AWS stack, one database, one bucket - there is no per-developer isolation. Slides and annotations
are shared, and a delete removes them for everyone.

What each person needs:

1. **Their own AWS key pair, and the tunnel running.** The database is reached only through an SSM
   tunnel that forwards `localhost:15432` to RDS, from any network. Whoever owns the Terraform state adds
   their name to `developers` once and sends them their key pair plus the `tunnel_command` line;
   setup on their side is in [`infra/README.md`](infra/README.md#adding-a-teammate). The key pair can
   open the tunnel and nothing else.
2. **A copy of `backend/.env.local`.** It is gitignored on purpose: it holds the RDS master password
   and the IAM secret key. Pass it along out of band, never through the repository.

Then `docker compose up -d --build` in `backend/`, and `cp .env.example .env.local && npm install &&
npm run dev` in `frontend/`. The `admin` account already exists in RDS - log in with the `ADMIN_*`
values from `.env.local`.

**Only one person runs Terraform.** `infra/terraform.tfstate` is local and gitignored, so a second
`terraform apply` from another machine starts from empty state and tries to build everything again,
failing with `DBInstanceAlreadyExists` and `BucketAlreadyOwnedByYou`. And `terraform destroy` from
anyone takes the database and every slide with it - see [`infra/README.md`](infra/README.md).

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
`error_message`). Ingestion runs on a dedicated worker thread, so a large slide never occupies the threadpool
that serves Deep Zoom tiles; it lives in the API process, so a restart marks anything still `processing` as
`failed` ("Ingestion was interrupted").

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

## Type checks

```bash
uv run mypy app          # strict type checking
```

## Logs

The app logs one JSON line per event to stdout (see `app/core/logging.py`): every request gets a
`request completed` (or `request failed`) line with method, path, status code and duration, tagged
with a `request_id` that's also echoed back as the `X-Request-ID` response header, so all log lines
for one request can be correlated. Set `LOG_LEVEL=DEBUG` in `.env.local` for more verbose output
while diagnosing an issue.

Read them with `docker compose logs -f api`, e.g. piped through `jq` to filter:

```bash
docker compose logs -f --no-log-prefix api | jq 'select(.status_code >= 500)'
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
| `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL connection; `DATABASE_URL` is built from these (see `app/core/config.py`). They point at the SSM tunnel (`localhost:15432`) and come from `terraform output -raw env_local`; compose replaces `POSTGRES_SERVER` with `host.docker.internal` |
| `BUCKET_ENDPOINT` | S3 regional endpoint; the browser uses the same one, because presigned URLs are signed for it |
| `BUCKET_ACCESS_KEY`, `BUCKET_SECRET_KEY`, `BUCKET_NAME`, `BUCKET_REGION` | Object storage credentials, bucket and region |
| `SLIDE_CACHE_DIR` | Directory holding local copies of slides |

`docker-compose.yml` loads every service's config straight from `.env.local` via `env_file:`, so
`docker compose up` needs no `--env-file` flag and can't silently see blank credentials the way
shell-style `${VAR}` interpolation could.

Only deployment-specific values are settings. Fixed parameters (Deep Zoom tile size, thumbnail size, tissue
detection thresholds, limits, error messages) are UPPER_CASE constants at the top of the module that uses them.

To add new variables, add them to the `Settings` class in `app/core/config.py`, and then set their values in your `.env.local` file.
