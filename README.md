# HistAno backend

The API of HistAno, built on **FastAPI**. It takes whole slide image (WSI) uploads, serves them as Deep Zoom tiles
with **OpenSlide**, cuts hotspots (small image crops) out of them, and stores the cell annotations experts draw on
those hotspots. It also runs reannotation and inter-expert agreement, accounts and organizations, and hands hotspots to
the ML worker for automatic cell segmentation. Data lives in **PostgreSQL**, files in S3-compatible object storage.

This repository also holds the production infrastructure (`infra/`, Terraform) and the deployment of the backend.

## HistAno

HistAno is a system for annotating cells in histopathology slides: a model outlines and classifies the cells of each
hotspot first, and experts correct its work instead of drawing every cell from scratch. It is split into three
repositories:

| Repository | What it is |
|---|---|
| [backend](https://github.com/HIST-Annotation-System/backend) | This API, its database and storage; the AWS infrastructure (`infra/`) |
| [frontend](https://github.com/HIST-Annotation-System/frontend) | The React web app |
| [ml-worker](https://github.com/HIST-Annotation-System/ml-worker) | The GPU worker that segments cells with Classpose (optional) |

Clone them side by side in one folder (`backend/`, `frontend/`, `ml-worker/`): the frontend's end-to-end tests run
the API from `../backend`.

```text
browser ──> frontend (React) ──/api/v1──> API (FastAPI) ──> PostgreSQL
   │                                        │ └──────────> object storage (slides, hotspot images, model results)
   └── uploads straight to object storage   └── broker ──> ml-worker (GPU) ──> broker ──> segmentation consumer
       (presigned URLs)                         jobs                            results      (saves the cells)
```

| | Local development | Production |
|---|---|---|
| Database | PostgreSQL container | AWS RDS |
| Object storage | S3Mock container | AWS S3 |
| Broker | Redis container | AWS SQS |
| API and consumer | `docker-compose.yml` | `docker-compose.prod.yml` on EC2, behind CloudFront |

## Quick start

Requirements: [Docker](https://www.docker.com/) with Docker Compose. That is all for running the stack; for running
the API outside Docker, also [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and the OpenSlide binaries).

```bash
cp .env.example .env.local      # works as is
docker compose up -d --build
```

This starts, each keeping its data in a named volume:

| Service | Address | Purpose |
|---|---|---|
| `postgres` | `localhost:5434` | The database (not 5432, which a native PostgreSQL often holds) |
| `s3` | `localhost:9090` | [S3Mock](https://github.com/adobe/S3Mock), an S3 emulator with the bucket `wsi-slides` |
| `redis` | `localhost:6379` | The broker for the optional ml-worker |
| `migrate`, `create-admin` | | One-shot: `alembic upgrade head`, then the admin account on a fresh database |
| `api` | http://localhost:8000 | The API, reloading on changes to the mounted source |

The API documentation is at http://localhost:8000/docs (Swagger UI) and http://localhost:8000/redoc.

Then start the [frontend](https://github.com/HIST-Annotation-System/frontend#quick-start) and log in with
`ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env.local` (`admin` / `admin123`). To try the viewer, upload a slide from
the [OpenSlide test data](https://openslide.cs.cmu.edu/download/openslide-testdata/), e.g. `Aperio/CMU-1-Small-Region.svs`.

Stop it with `docker compose down`; add `-v` to start over with an empty database and bucket. Nothing here touches the
deployed app's data.

`.env.local` holds the addresses as seen from your machine; compose gives its containers the service names instead
(`x-in-docker` in `docker-compose.yml`). The browser uploads straight to S3Mock with presigned URLs signed for
`BUCKET_PUBLIC_ENDPOINT` (`localhost:9090`), while the API reaches it as `s3:9090`.

## Development

### Running the API outside Docker

With the stack's services running (`docker compose up -d postgres s3 redis`) and the same `.env.local`:

```bash
uv sync
uv run alembic upgrade head
uv run python -m scripts.create_admin     # once: creates the admin account
uv run uvicorn app.main:app --reload --no-access-log
```

### Tests and checks

```bash
uv run --with pytest python -m pytest tests   # plain `pytest` would not put the project root on sys.path
uv run mypy app                               # strict type checking; not yet enforced by CI
uv run alembic check                          # fails if the models and the migrations have drifted (needs postgres)
```

The tests need no running service and read the settings from `.env.local` (CI copies `.env.example`). They are in
`tests/unit` (pure functions), `tests/services`, `tests/api` (routers through FastAPI's test client) and `tests/tasks`.
End-to-end tests of the whole app live in the frontend repository (`e2e/`).

`benchmarks/` measures the CPU-bound services in-process (`uv run python -m benchmarks.run_benchmarks --quick`); the
last results are in `benchmarks/BENCHMARK_RESULTS.md`.

### Database migrations

```bash
uv run alembic revision --autogenerate -m "describe change" --rev-id 0028_describe_change
uv run alembic upgrade head
```

Name revisions `<next number>_<what changed>`, and review the generated file: autogenerate misses data migrations and
some type changes. A migration merged to `main` runs on the production database at the next deploy.

### Project structure

```text
app/
  main.py                FastAPI app: lifespan, middleware, routers
  core/                  settings (config.py), logging, Celery app, constants
  dependencies/          process-wide resources created at startup (resources.py) and per-request providers
  routers/               endpoints, grouped by resource; no business logic
  schemas/               Pydantic request and response models
  services/              business logic: upload, ingestion, tiling, hotspots, annotations, agreement, segmentation
  repositories/          database queries
  models/                SQLAlchemy models
  exceptions/            application errors and the handler that turns them into HTTP responses
  middleware/            request logging
  segmentation_consumer.py   the Celery worker that saves the ml-worker's results
alembic/                 migrations
scripts/create_admin.py  creates the admin account
tests/                   pytest suite
benchmarks/              performance benchmarks
deploy/, docker-compose.prod.yml, .github/workflows/   production deployment
infra/                   AWS infrastructure (Terraform)
```

Every endpoint addresses slides by id (`/slides/{slide_id}/...`); there is no "current slide". Infrastructure (the
database engine, S3 clients, the cache of opened slides) is created once in the FastAPI lifespan and injected through
dependencies.

Only deployment-specific values are settings. Fixed parameters (tile size, thumbnail size, tissue detection
thresholds, limits, error messages) are UPPER_CASE constants at the top of the module that uses them.

### Logs

The app logs one JSON line per event to stdout (`app/core/logging.py`). Every request gets a `request completed` (or
`request failed`) line with method, path, status code and duration, tagged with a `request_id` that is also returned
as the `X-Request-ID` header, so all lines of one request can be found. `LOG_LEVEL=DEBUG` gives more detail.

```bash
docker compose logs -f --no-log-prefix api | jq 'select(.status_code >= 500)'
```

## Configuration

Settings are read by `pydantic-settings` (`app/core/config.py`) from environment variables, then from `.env.local`.
`docker-compose.yml` passes `.env.local` to every service with `env_file:`. In production the whole file is written
from AWS SSM Parameter Store at each deploy (see [`infra/README.md`](infra/README.md)).

| Variable | Purpose |
|---|---|
| `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | The database; the password must not contain `/ " @ # ?` or a space |
| `BUCKET_ENDPOINT`, `BUCKET_ACCESS_KEY`, `BUCKET_SECRET_KEY`, `BUCKET_NAME`, `BUCKET_REGION` | Object storage as the API reaches it |
| `BUCKET_PUBLIC_ENDPOINT` | Where the browser reaches the bucket, if not `BUCKET_ENDPOINT` (only locally); presigned URLs are signed for it |
| `SLIDE_CACHE_DIR` | Local copies of slides (OpenSlide needs files on disk) |
| `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` | Login tokens, kept in HTTP-only cookies |
| `COOKIE_SECURE` | Send the cookies over HTTPS only; `true` in production, `false` over plain HTTP |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | The administrator `create_admin` creates when the account does not exist |
| `BACKEND_CORS_ORIGINS` | Origins allowed to call the API from a browser (the Vite dev server) |
| `SEGMENTATION_ENABLED` | Send hotspots to the ml-worker (see [Cell segmentation](#cell-segmentation)) |
| `CELERY_BROKER_URL`, `CELERY_BROKER_TRANSPORT_OPTIONS` | The broker shared with the ml-worker: Redis locally, SQS in production |
| `LOG_LEVEL` | `INFO` by default |

To add one, add it to `Settings`, to `.env.example`, and, for production, to `aws_ssm_parameter.backend_env` in
`infra/backend.tf`.

## How it works

### Organizations and roles

An organization owns its slides and hotspot folders; its members see nothing of other organizations. There are three
roles: `ADMIN` (the system administrator, belongs to no organization, manages all of them and the shared dictionaries:
cell types, tags, priority weights), `ORG_ADMIN` (manages the users and agreement settings of one organization) and
`ANNOTATOR`. New accounts set their own password and profile on first login.

### Slides

Only formats the bundled OpenSlide reads natively are accepted (`app/services/slide_formats.py`), and every slide
needs resolution metadata (MPP) and, above 10,000 px, a resolution pyramid. Anything else is rejected: by name when
the upload starts, by content during ingestion (the slide ends `failed` with the reason).

| Kind | Formats | Upload as |
|---|---|---|
| Single file | Aperio `.svs`, pyramidal tiled `.tif`/`.tiff`, Hamamatsu `.ndpi`, Leica `.scn`, Ventana `.bif`, Sakura `.svslide`, Zeiss `.czi` | The file itself, never inside an archive |
| Multi-file | MIRAX `.mrxs`, Hamamatsu `.vms`/`.vmu`, DICOM WSI `.dcm` | One `.zip` with exactly one slide and nothing else |

Archive layouts (`__MACOSX/`, `.DS_Store` and `Thumbs.db` are ignored):

```text
CMU-1.zip                slide.zip                series.zip
├─ CMU-1.mrxs            ├─ slide.vms             └─ series/
└─ CMU-1/                ├─ slide.opt                ├─ level-0.dcm
   ├─ Slidedat.ini       └─ slide_*.jpg              └─ level-1.dcm ...
   └─ *.dat              (.vmu: index + .ngr files in one folder; DICOM files need the .dcm extension)
```

Not supported: plain images (`.jpg`, `.png`, `.bmp`, ...), flat or OME-TIFF, stripped TIFF, Trestle, Olympus `.vsi`,
Philips iSyntax and nested archives. Convert them to a pyramidal tiled TIFF with resolution first, e.g. with QuPath or
`vips tiffsave in.tif out.tif --tile --pyramid --compression jpeg`.

**Upload.** The browser uploads the file in parts straight to the bucket with presigned URLs (`/upload/*`), so large
slides never pass through the API. Objects already in the bucket can be registered with `/upload/import-from-storage`.

**Ingestion.** After the upload completes, a background job copies the slide into the slide cache (`SLIDE_CACHE_DIR`,
extracting archives safely), validates it with OpenSlide, and stores its metadata, a thumbnail and the bounding box of
the detected tissue. The status goes `uploading` → `processing` → `ready` (or `failed` with an `error_message`).
Ingestion runs on its own thread pool, so a large slide never holds up tile requests; a restart of the API marks
anything still `processing` as `failed` ("Ingestion was interrupted").

**Slide cache.** OpenSlide needs files on disk, so slides are copied out of the bucket on first use. When a viewed
slide is not cached, the API starts copying it in the background and answers `503` with code `slide_preparing`
(`Retry-After: 5`) until it is; the viewer shows that and retries. When the disk fills up, the least recently used
slides are evicted, never one viewed in the last 15 minutes.

### Hotspots

A hotspot is a square image crop that experts annotate. It is cut out of a slide (squares chosen in the slide viewer
are cut out by a background `hotspot_batch`) or uploaded as a PNG/JPEG, and belongs to a folder of the hotspot
catalog. The catalog sorts hotspots by their estimated priority (`app/services/priority.py`, weighted by the admin),
image descriptors (`app/services/hotspot_features.py`), the model's uncertainty or the experts' tags.

Annotations belong to a hotspot: GeoJSON polygons (with an optional Bézier curve) in the hotspot image's pixels, each
either a cell of a cell type or an excluded area. They export as GeoJSON with QuPath-style properties.

### Reannotation and agreement

Several annotators can reannotate the same hotspot, each in a layer of their own started from the model's cells
(`app/services/hotspot_reannotation.py`). Once enough have confirmed their layer, their agreement (Fleiss' kappa or IoU,
`app/services/agreement.py`) is compared with the organization's threshold to decide whether the hotspot counts as
reannotated.

### Cell segmentation

Every hotspot is pre-filled with classified cell outlines by Classpose, which runs in the separate
[ml-worker](https://github.com/HIST-Annotation-System/ml-worker); its README has the contract between the two.

**It is optional.** With `SEGMENTATION_ENABLED=false`, the local default, nothing is sent: hotspots stay `pending` and
everything else works. To use it locally, start the ml-worker's `local` profile, set `SEGMENTATION_ENABLED=true` in
`.env.local` and run `docker compose --profile segmentation up -d --build`, which adds the consumer that saves the
results. Production always has it on.

| `segmentation_status` | Meaning |
|---|---|
| `pending` | Waiting for a worker |
| `done` | The model's cells are saved |
| `failed` | The model could not process it (`segmentation_error` says why); not retried automatically |
| `skipped` | Someone saved a cell by hand first, so model cells would land on top of their work |

How it stays out of the way:

- **No request depends on it.** A background thread in the API (`app/services/segmentation.py`) sends pending
  hotspots. If the broker is down it logs one warning and tries again; the hotspots just wait.
- **Lost jobs recover by themselves.** A hotspot still `pending` 30 minutes after being sent goes out again. Jobs
  expire after the same time, so a stopped worker doesn't come back to a pile of duplicates.
- **Nothing is sent twice.** Claiming a hotspot locks its row, so several API processes on one database never send
  the same one.

Model cells are annotations with `source = model` and the cell type the model predicted: its type names are matched to
existing cell types, ignoring case, and created when missing. A cell it gave no type gets `Unclassified`, which can't
be renamed or deleted. Editing one makes it `source = human`, credited to the editor. Stats and `annotation_count`
only count `human` rows; the editor and the GeoJSON export show both. The model's original output stays in
`hotspots/{id}/segmentation.json`.

**The model's uncertainty.** The model also reports how sure it is of each cell's outline and type. Every
segmentation stores the means over the hotspot's cells (`model_outline_confidence`, `model_class_confidence`), and the
catalog can sort by uncertainty (1 - confidence), most uncertain first. Hotspots segmented before this have no values:
a manager rescores them a folder at a time (`POST /hotspot-folders/{id}/confidence-rescore`, the gauge button next to
the folder). Each goes to the worker once more and only its two means change, never its cells. Rescores go out after
any pending hotspot, at most `RESCORES_IN_FLIGHT` at a time; a lost one is sent again after
`SEGMENTATION_RESEND_AFTER`, and a failed one ends with `confidence_error` and keeps the old values.

## API

All endpoints are under `/api/v1` and, except login, need a logged-in user; `GET /health` (outside it) is the health check. The full reference with
request and response schemas is the generated OpenAPI documentation at `/docs`.

| Prefix | Router | Purpose |
|---|---|---|
| `/auth` | `auth.py` | Login, logout, token refresh, first-login profile setup, the current user |
| `/organizations`, `/users`, `/categories` | `organizations.py`, `users.py`, `categories.py` | Organizations, accounts, annotator categories |
| `/upload` | `upload.py` | Multipart upload of slides with presigned URLs, import from the bucket |
| `/slides` | `slides.py` | Slide library, metadata, Deep Zoom descriptor and tiles, thumbnail, region export |
| `/storage` | `storage.py` | Browsing the bucket (admin) |
| `/hotspots`, `/hotspot-folders`, `/hotspot-batches`, `/hotspot-boxes` | `hotspots.py` | Hotspot catalog and images, folders, cutting hotspots out of slides, annotations, reannotation, agreement |
| `/tags`, `/cell-types`, `/priority-weights` | `tags.py`, `cell_types.py`, `priority_weights.py` | Shared dictionaries, managed by the admin |
| `/stats` | `stats.py` | Annotation statistics |

Errors have the body `{"detail": "<message>"}`, plus `"code"` where the frontend reacts to it (e.g. `slide_preparing`);
see `app/exceptions/errors.py`.

## Deployment

Production is one EC2 host running `docker-compose.prod.yml` (migrations, API, segmentation consumer) against RDS, S3
and SQS, behind CloudFront, which also serves the frontend; the ml-worker runs on a separate GPU machine. Every push to
`main` deploys: `.github/workflows/deploy.yml` runs the tests, builds the ARM image, pushes it to ECR and runs
`deploy/deploy.sh` on the host through SSM, which applies the migrations before starting the new image. So **merging
to `main` migrates the production database.**

The workflow reads `AWS_ROLE_ARN`, `ECR_REPOSITORY` and `INSTANCE_ID` from the repository's variables and gets
temporary AWS credentials through GitHub OIDC; no secret is stored in GitHub. To roll back, run
`/opt/wsi/deploy.sh <older image>` on the host; ECR keeps the last 10 images. The infrastructure, its operation and its
cost are described in [`infra/README.md`](infra/README.md).

## Contributing

- `main` is production: every push to it deploys. `dev` is the integration branch.
- Branch off `dev` (`feat/<topic>`, `bugfix/<topic>`) and open a pull request into `dev`. CI
  (`.github/workflows/ci.yml`) runs the tests on every pull request and every push to `dev`.
- Release by opening a pull request from `dev` into `main`; check what migrations it brings first.
- Commit messages: `<type>: <summary>`, with `feat`, `bugfix`, `perf`, `test`, `style`, `docs` or `infra`.
- A change to the segmentation contract needs a matching change in the ml-worker (see its README).
- Never commit secrets: `.env.local` and `infra/terraform.tfvars` are gitignored.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `env file .../.env.local not found` | Run `cp .env.example .env.local`. |
| Login fails after changing `ADMIN_PASSWORD` | The admin is only created when missing; the old password still applies. `docker compose down -v` starts over. |
| The viewer says the server is copying the slide | Normal on a slide's first view, or after it was evicted from the cache. |
| Hotspots stay `pending` | `SEGMENTATION_ENABLED` is `false`, the `segmentation` profile is not running, or no ml-worker is. |
| The ml-worker can't reach Redis or S3Mock on Linux | Set `BIND_ADDRESS` to the docker0 address (usually `172.17.0.1`) before `docker compose up`. |
| The API container doesn't pick up a new setting | `docker compose up -d` recreates it; `restart` keeps the old environment. |

## License

[MIT](LICENSE).
