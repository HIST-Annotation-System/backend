# Backend API (FastAPI)

Backend for the Whole Slide Image (WSI) viewer, built on **FastAPI**. It stores slides in S3-compatible object
storage (**AWS S3** in production, S3Mock locally), keeps slide metadata and annotations in **PostgreSQL** (RDS in
production), and serves Deep Zoom tiles with **OpenSlide**.
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

Database migrations live in `alembic/`, the Terraform config for the AWS resources in `infra/`, and the production
deployment in `docker-compose.prod.yml`, `deploy/` and `.github/workflows/`.

### Statelessness

Every endpoint addresses slides by id (`/slides/{slide_id}/...`); there is no "current slide". Infrastructure
(DB engine, S3 clients, the cache of opened slides) is created once in the FastAPI lifespan and injected through
dependencies. Opened OpenSlide handles are cached in a bounded LRU keyed by the immutable slide id - a pure
performance cache that any worker may rebuild at any time.

## Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- For running outside Docker: [uv](https://docs.astral.sh/uv/) (OpenSlide binaries come from the `openslide-bin` wheel)

## Local development

Everything runs on your machine: `docker-compose.yml` brings its own PostgreSQL, an S3 emulator
([S3Mock](https://github.com/adobe/S3Mock)) and Redis, so local work never touches the deployed data.

1. **Create the environment file:** `cp .env.example .env.local`. It works as is.
2. **Start the stack:**
   ```bash
   docker compose up -d --build
   ```
   This starts:
   - `postgres` (published on `localhost:5434`), `s3` (S3Mock on `localhost:9090`, bucket `wsi-slides`) and `redis`
     (`localhost:6379`, the broker for the optional ml-worker), each keeping its data in a named volume,
   - `migrate` (`alembic upgrade head`) and `create-admin` (creates `ADMIN_USERNAME` on a fresh database), both one-shot,
   - `api` - the FastAPI server at `http://localhost:8000` (with `--reload` on the mounted source).

   Then `cp .env.example .env.local && npm install && npm run dev` in `frontend/`, and log in with the `ADMIN_*` values.
3. **Stop it:** `docker compose down` (add `-v` to start over with an empty database and bucket).

`.env.local` holds the addresses as seen from the host; compose gives its containers the service names instead
(`x-in-docker` in `docker-compose.yml`). The browser uploads straight to S3Mock with presigned URLs signed for
`BUCKET_PUBLIC_ENDPOINT` (`localhost:9090`), while the API reaches it as `s3:9090`.

### Running outside Docker

With the stack's `postgres`, `s3` and `redis` running (`docker compose up -d postgres s3 redis`) and the same `.env.local`:

```bash
uv sync
uv run alembic upgrade head
uv run python -m scripts.create_admin     # once: creates the admin account
uv run uvicorn app.main:app --reload --no-access-log
```

## Deployment

Production is one EC2 host running `docker-compose.prod.yml` (API, migrations, segmentation consumer) against RDS, S3
and SQS, behind CloudFront, which also serves the frontend; the GPU worker runs on a separate machine. Every push to
`main` deploys: `.github/workflows/deploy.yml` builds the ARM image, pushes it to ECR and runs `deploy/deploy.sh` on the
host through SSM, which applies the migrations first. So **merging to `main` migrates the production database**.
The workflow reads `AWS_ROLE_ARN`, `ECR_REPOSITORY` and `INSTANCE_ID` from the repository's variables. To roll back,
run `/opt/wsi/deploy.sh <older image>` on the host (`aws ssm start-session`); ECR keeps the last 10 images.

The AWS resources are Terraform in `infra/`, applied from one machine (its state is local); see
[`infra/README.md`](infra/README.md). The region is pinned to `eu-north-1` by an organization SCP that denies every
other one.

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

## Cell segmentation

Every hotspot, uploaded or cut from a slide, is pre-filled with classified cell outlines by Classpose. The model runs in the
separate `ml-worker` repository, whose README has the setup and the contract between the two.

**It is optional.** With `SEGMENTATION_ENABLED=false`, the default, nothing is sent and hotspots stay `pending`, and
everything else works as usual. To use it on a machine running the `ml-worker` stack, set it to `true` in `.env.local`
and start with `docker compose --profile segmentation up -d --build`, which adds the consumer that saves results.

| `segmentation_status` | Meaning |
|---|---|
| `pending` | Waiting for a worker |
| `done` | The model's cells are saved |
| `failed` | The model could not process it (`segmentation_error` says why); not retried automatically |
| `skipped` | Someone saved a cell by hand first, so model cells would land on top of their work |

How it stays out of the way:

- **No request depends on it.** A background thread in the API (`app/services/segmentation.py`) sends pending hotspots.
  If Redis is down it logs one warning and tries again; the hotspots just wait.
- **Lost jobs recover by themselves.** A hotspot still `pending` 30 minutes after being sent goes out again. Jobs expire
  after the same time, so a stopped worker doesn't come back to a pile of duplicates.
- **Stacks share the work.** All of them see the same database, and claiming a hotspot locks its row, so two stacks don't
  send the same one.

Model cells are annotations with `source = model` and the cell type the model predicted: its type names are matched to
existing cell types, ignoring case, and created when missing. A cell it gave no type gets `Unclassified`, which can't be
renamed or deleted.
Editing one makes it `source = human`, credited to the editor. Stats and `annotation_count` only count `human` rows; the
editor and the GeoJSON export show both. The model's original output stays in `hotspots/{id}/segmentation.json`.

### The model's uncertainty

The model also reports how sure it is of each cell's outline and type. Every segmentation stores the means over the
hotspot's cells (`model_outline_confidence`, `model_class_confidence`), and the catalog can sort by the uncertainty
(1 - confidence) of the outlines, of the cell types, or of both together, most uncertain first.

Hotspots segmented before this have no values. A manager rescores them a folder at a time
(`POST /hotspot-folders/{id}/confidence-rescore`, the gauge button next to the folder): each segmented hotspot goes to
the worker once more, only its two means change, and never its cells. Rescores go out after any pending hotspot, at
most `RESCORES_IN_FLIGHT` at a time, so the queue never grows past what the worker reaches before a job expires. A lost
rescore is sent again after `SEGMENTATION_RESEND_AFTER`; a failed one, or one whose cells another model version drew,
ends with `confidence_error` and keeps the old values.

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
2. `.env.local` file (locally; production writes the whole file from SSM Parameter Store at each deploy)

The main variables are listed in `.env.example`:

| Variable | Purpose |
|---|---|
| `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL connection; `DATABASE_URL` is built from these (see `app/core/config.py`) |
| `BUCKET_ENDPOINT` | Object storage endpoint as the API reaches it |
| `BUCKET_PUBLIC_ENDPOINT` | Endpoint the browser reaches the bucket at, if different (only locally); presigned URLs are signed for it |
| `BUCKET_ACCESS_KEY`, `BUCKET_SECRET_KEY`, `BUCKET_NAME`, `BUCKET_REGION` | Object storage credentials, bucket and region |
| `SLIDE_CACHE_DIR` | Directory holding local copies of slides |
| `SEGMENTATION_ENABLED` | Send hotspots to the `ml-worker` stack; only on a machine running it (see [Cell segmentation](#cell-segmentation)) |
| `CELERY_BROKER_URL`, `CELERY_BROKER_TRANSPORT_OPTIONS` | The broker shared with `ml-worker`: the local stack's Redis, or SQS in production |
| `COOKIE_SECURE` | Send the auth cookies over HTTPS only; `true` in production |

`docker-compose.yml` loads every service's config straight from `.env.local` via `env_file:`, so
`docker compose up` needs no `--env-file` flag and can't silently see blank credentials the way
shell-style `${VAR}` interpolation could.

Only deployment-specific values are settings. Fixed parameters (Deep Zoom tile size, thumbnail size, tissue
detection thresholds, limits, error messages) are UPPER_CASE constants at the top of the module that uses them.

To add new variables, add them to the `Settings` class in `app/core/config.py`, and then set their values in your `.env.local` file.
