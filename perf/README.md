# Performance tests (k6)

Capacity tests of the deployed app (`https://d3s9lf5kpej9we.cloudfront.net`, built from `main`): what each operation
costs, how many experts can work at once, what slows down first, and how long experts wait for the GPU model.
They run by hand from a laptop; they are not part of CI.

**Everything runs against the one deployment the whole team uses.** Announce a test window, and remember that stress
and spike runs can make the app unusable for a while.

## What is here

| Path | What it is |
|---|---|
| `iso/tiles.js` | Tiles alone, rising rate until it breaks: max tiles/s of the single API process |
| `iso/annotation.js` | Cell saves at a rising rate, then 1 → 5 teams confirming at once (agreement computation) |
| `iso/gpu.js` | One burst of 30 hotspots, waiting until the GPU worker has segmented all of them |
| `iso/import.js` | One slide import (download from S3 + processing on the host) |
| `workload.js` | The realistic mix: protocol experts make the load, 3 browser VUs measure what users see. One script for every load shape: `PROFILE=smoke\|average\|stress\|spike\|soak` |
| `journeys/` | The expert session (API) and the browser journey (Chromium) |
| `lib/` | Settings, API client, Deep Zoom viewport, annotation edits, test data lookup |
| `setup/seed.js`, `setup/cleanup.js` | Creating and removing the test data |
| `run.ps1` | Runs one test and keeps its outputs in `results/<run>/` |
| `grafana/wsi-perf.json` | Grafana dashboard: k6 next to host, container and log data |

### The expert session (`workload.js`, scenario `experts`)

One iteration is one 4–7 minute work session, like the presentation's virtual expert:

1. sign in (once per VU, again after 25 min), `GET /users/me`, the slide list and thumbnails;
2. a slide in the viewer: 6–12 pan/zoom moves of ~30 tiles each (a 1600×900 screen), 3–8 s apart, sometimes the
   live image descriptors of squares (`/hotspot-boxes/features`);
3. the team's hotspot (VUs 1–3 are team 1, ...): image, cells, agreement, start or resume reannotation, 20–40 cell
   edits ~8 s apart (mostly reshaping, some adding and removing in equal numbers), then confirming.

All the while the slide list is polled every **30 s** (the frontend's real idle rate; the presentation said 3 s).
Images are fetched once per VU, like a browser cache does with the API's `immutable` images.

### Thresholds

| What | Limit |
|---|---|
| API JSON (`kind:api`), p95 | < 500 ms |
| Tile (`kind:tile`), p95 | < 1 s (stress and iso runs abort above 3 s) |
| Cell save (`kind:save`), p95 | < 500 ms |
| Failed requests | < 1 % (stress and iso runs abort above 5 %) |
| Browser: viewer shows its first 10 tiles (`viewer_ready`), p95 | < 3 s |
| Browser: Largest Contentful Paint, p95 | < 2.5 s |
| Agreement, box features, segmentation, import | recorded only |

`viewer_ready` includes the laptop's own rendering: compare it with the `smoke` run, which has no load.

## One-off setup

### 1. Tools

- **k6 v2.4.0**: `winget install GrafanaLabs.k6 --version 2.4.0` (the browser VUs use the installed Chrome).
- **AWS CLI**, signed in to the project account, for the test slide files (and, optionally, to record the deployed
  image of each run).

### 2. Grafana Cloud (free tier)

1. Create a free Grafana Cloud stack, then collect five values. They are on grafana.com (the Cloud Portal), not inside
   Grafana itself: sign in at grafana.com, open your organization and the stack (*Manage stack*).

   | Value | Where |
   |---|---|
   | Prometheus push URL | **Prometheus** card → **Details** → *Remote Write Endpoint* (ends in `/api/prom/push`) |
   | Prometheus user | same page → *Username / Instance ID* (a number) |
   | Loki push URL | **Loki** card → **Details** → *URL*, plus `/loki/api/v1/push` |
   | Loki user | same page → *User* (another number) |
   | Token | Cloud Portal → **Security** → **Access Policies** → **Create access policy**: realm = your stack, scopes `metrics:write` and `logs:write` (use **Add scope** if one is not listed) → **Create** → **Add token** → copy it (shown only once) |

   Same values from inside Grafana, if the portal looks different: **Connections → Data sources →
   grafanacloud-*stack*-prom** shows the Prometheus URL (add `/push`) and the user under *Basic authentication*;
   **grafanacloud-*stack*-logs** shows the Loki URL (add `/loki/api/v1/push`) and its user. The token can also be made
   in **Administration → Users and access → Cloud access policies**.
2. Fill `grafana_cloud` in `backend/infra/terraform.tfvars` (see `terraform.tfvars.example`) and run
   `terraform apply` in `backend/infra`. It only changes the app's settings parameter.
3. Merge the `alloy` service of `docker-compose.prod.yml` to `main`. The deploy starts it on the host; it pushes host
   metrics, per-container metrics and all container logs (memory-capped at 200 MB).
4. In Grafana: *Dashboards → New → Import* `grafana/wsi-perf.json`, choosing the stack's Prometheus and Loki.
5. For k6's own metrics, set these in the shell you run k6 from (same Prometheus details as above):

   ```powershell
   $env:K6_PROMETHEUS_RW_SERVER_URL = "https://prometheus-prod-XX-....grafana.net/api/prom/push"
   $env:K6_PROMETHEUS_RW_USERNAME = "<prometheus user>"
   $env:K6_PROMETHEUS_RW_PASSWORD = "<token>"
   ```

   Without them, runs still produce the local HTML report. Grafana Cloud's free tier keeps data for 14 days; the
   HTML reports in `results/` stay.

### 3. Test slides

Public OpenSlide test slides, copied into the bucket under `perf/` (the bucket is `bucket_name` in terraform.tfvars):

```powershell
$bucket = "s3://hist-annotation-images-zpi"
curl.exe -LO https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-1.svs
curl.exe -LO https://openslide.cs.cmu.edu/download/openslide-testdata/Aperio/CMU-2.svs
curl.exe -LO https://openslide.cs.cmu.edu/download/openslide-testdata/Mirax/Mirax2.2-1.zip
Expand-Archive Mirax2.2-1.zip -DestinationPath Mirax2.2-1
aws s3 cp CMU-1.svs "$bucket/perf/CMU-1.svs"
aws s3 cp CMU-2.svs "$bucket/perf/CMU-2.svs"
aws s3 cp Mirax2.2-1 "$bucket/perf/Mirax2.2-1/" --recursive   # the .mrxs file and its folder, side by side
```

### 4. Credentials

k6 reads settings from the shell's environment (or `-e NAME=value`). Set them once per shell; never commit them:

```powershell
$env:K6_USER_PASSWORD = "<password for all k6 accounts, min. 8 characters>"
$env:K6_ADMIN_USER = "admin"                 # only for seed, import and the final cleanup
$env:K6_ADMIN_PASSWORD = "<admin password>"
```

### 5. Seed

With the teammate's **GPU worker running** (`docker compose logs -f worker-aws` shows `ready`):

```powershell
k6 run -e SLIDE_KEYS=perf/CMU-1.svs,perf/CMU-2.svs,perf/Mirax2.2-1/ setup/seed.js
```

It creates organization `k6-perf`, `k6-manager` and `k6-expert-001..130`, imports the slides and cuts 44 hotspots
(one per team of three) into folder `k6-pool`, then waits until the model has segmented them. It is idempotent: run it
again to top anything up (it also replaces pool hotspots the model failed on).

## Running the tests

### Before each test window

- Tell the team; nobody deploys to `main` during it.
- The GPU worker runs (needed for `iso/gpu.js`).
- The Grafana dashboard is open.
- Pre-flight: `.\run.ps1 workload.js smoke`. During every run, watch the laptop: k6 above
  ~80 % CPU or any *dropped iterations* (dashboard) means the laptop, not the server, is the limit.
- Optional: `$env:WSI_INSTANCE_ID = "<backend.INSTANCE_ID from terraform output github_variables>"` records the
  deployed image of each run in `results/<run>/image.txt`.

### Order

Each step only if the previous one passed.

| # | Command | Time |
|---|---|---|
| 1 | `.\run.ps1 workload.js smoke` | ~7 min |
| 2 | `.\run.ps1 iso\tiles.js` (`-e TILES_MAX_RATE=300`) | ≤ 15 min |
| 3 | `.\run.ps1 iso\annotation.js` (`-e SAVES_MAX_RATE=30`) | ~26 min |
| 4 | `.\run.ps1 iso\gpu.js` (`-e GPU_HOTSPOTS=30`) | until the GPU is done |
| 5 | `.\run.ps1 iso\import.js -e IMPORT_KEY=<copy>` (below) | minutes |
| 6 | `.\run.ps1 workload.js average` | 18 min |
| 7 | `.\run.ps1 workload.js stress` | up to 45 min, ends at the breaking point |
| 8 | `.\run.ps1 workload.js spike` | 9 min |
| 9 | `.\run.ps1 workload.js soak` | 2 h 3 min |

Every test opens each slide in its setup (not measured), so the measured part never waits for a slide to be downloaded
to the host. The import deletes its slide afterwards, **and with it the file**, so it imports a throw-away copy:

```powershell
$key = "perf/import/k6-import-CMU-2-$(Get-Date -Format yyyyMMddHHmm).svs"
aws s3 cp "$bucket/perf/CMU-2.svs" "$bucket/$key"
.\run.ps1 iso\import.js -e IMPORT_KEY=$key
```

If the API stops answering: `aws ssm start-session --target <instance>` (`terraform output shell_command`), then
`cd /opt/wsi; docker compose -f docker-compose.prod.yml -p wsi restart api`.

### Results

- `results/<run>/report.html`: k6's report (open in a browser), `summary.txt`: the printed summary with thresholds.
- Grafana: pick the run in *Run*; the rows show load and response times, what browser users saw, the host, each
  container, and API log lines for errors and requests slower than 1 s.

## Cleanup

```powershell
k6 run setup/cleanup.js
```

removes what an aborted run left behind (`k6-gpu-*` folders, `k6-import-*` slides). At the end of the campaign,

```powershell
k6 run -e ALL=1 setup/cleanup.js
```

deletes everything the seed made: the pool, the test slides **and their files in the bucket**, the accounts and the
organization.

## Cost

About $0–2 for the whole campaign, within the free tiers: CloudFront (10 M requests and 1 TB a month free), traffic
from EC2 to CloudFront (free), CPU credits of the burstable EC2 and RDS (cents at most), SQS (1 M requests free),
S3 (a few GB of test slides), Grafana Cloud free tier (no card; data over its limits is dropped, not billed).
