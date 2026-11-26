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
| `perf.ps1` | The one command: setup, all tests in order, one test, cleanup |
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

## How to run

Needs the AWS CLI, signed in to the project account. Everything else `perf.ps1` does itself: it installs k6, reads
the bucket, admin password and Grafana settings from `backend/infra/terraform.tfvars`, and generates the k6 accounts'
password into `perf/.local.env` (git-ignored; put `K6_ADMIN_PASSWORD=...` there if the admin password was changed
in the app).

```powershell
cd backend\perf
.\perf.ps1 setup      # once (safe to repeat): test slides into S3, accounts, 44 segmented hotspots
.\perf.ps1 all        # every test in order, ~2.5 h
```

- **The GPU worker must be running** (a teammate's PC, `docker compose logs -f worker-aws` shows `ready`) for `setup`
  and the `gpu` test. Both stop after 15 min without progress instead of waiting.
- **Tell the team first**: `all` loads the app everyone uses, and `stress` and `spike` can make it unusable for a while.
  Nobody should deploy to `main` meanwhile.
- `all` runs smoke, tiles, annotation, gpu, import, average, stress, spike, then removes the left-overs of the runs
  and prints a summary (also saved in `results/`). Crossed thresholds are results (the ramps are meant to find the
  limit); a test that breaks stops the sequence.
- One test: `.\perf.ps1 smoke` (or `tiles`, `annotation`, `gpu`, `import`, `average`, `stress`, `spike`, `soak`).
  The 2-hour `soak` is only run this way.
- Watch the laptop during runs: k6 above ~80 % CPU, or any *dropped iterations* on the dashboard, means the laptop
  and not the server is the limit.
- If the API stops answering: `terraform output shell_command` (in `backend/infra`) opens a shell on the host, then
  `cd /opt/wsi; docker compose -f docker-compose.prod.yml -p wsi restart api`.

| Test | What | Time |
|---|---|---|
| `smoke` | 1 expert session + 1 browser journey: scripts and thresholds work | ~7 min |
| `tiles` | tiles alone, rising rate until it breaks | ≤ 15 min |
| `annotation` | cell saves rising, then 1 → 5 teams confirming | ~26 min |
| `gpu` | 30 hotspots through the GPU pipeline | until the GPU is done |
| `import` | one slide import (a fresh copy of CMU-2.svs) | minutes |
| `average` | 15 experts for 15 min | 18 min |
| `stress` | 5 → 60 → 120 experts, ends at the breaking point | up to 45 min |
| `spike` | 0 → 60 experts in 30 s and back | 9 min |
| `soak` | 15 experts for 2 h | 2 h 3 min |

Every test opens each slide in its setup (not measured), so the measured part never waits for a slide to be
downloaded to the host.

### Results

- `results/<run>/report.html`: k6's report (open in a browser), `summary.txt`: the printed summary with thresholds,
  `image.txt`: the backend build that was measured.
- Grafana: pick the run in *Run*; the rows show load and response times, what browser users saw, the host, each
  container, and API log lines for errors and requests slower than 1 s.

### Cleanup

`.\perf.ps1 cleanup` removes what an aborted run left behind (`k6-gpu-*` folders, `k6-import-*` slides).
`.\perf.ps1 cleanup -All`, at the end of the campaign, deletes everything the setup made: the pool, the test slides
**and their files in the bucket**, the accounts and the organization.

### Grafana Cloud (once; already done)

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
5. k6's own metrics go to the same stack automatically: `perf.ps1` reads the Grafana settings from
   `terraform.tfvars`. Without them, runs still produce the local HTML report. Grafana Cloud's free tier keeps data
   for 14 days; the HTML reports in `results/` stay.
