// The GPU pipeline alone: one burst of hotspots cut out of a test slide, then waiting until the model has segmented
// every one of them (API -> SQS -> GPU worker -> SQS -> segmentation-consumer on the EC2 host -> database).
// Needs the GPU worker running. Its folder is deleted at the end (KEEP=1 keeps it).
//   k6 run -e GPU_HOTSPOTS=30 iso/gpu.js

import { fail, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { Client, orAbort, uuid } from '../lib/api.js';
import { GPU_FOLDER_PREFIX, MANAGER, SUMMARY_TREND_STATS, SYSTEM_TAGS, USER_PASSWORD, requireEnv } from '../lib/config.js';
import { boxGrid, largestSlide, readSlides } from '../lib/data.js';

const HOTSPOTS = Number(__ENV.GPU_HOTSPOTS || 30);
const POLL_S = 5;
const TIMEOUT_MS = 90 * 60 * 1000;
// No hotspot segmented for this long: the GPU worker is not running (the first job also loads the model).
const STALL_MS = 15 * 60 * 1000;

// Batch request -> all hotspots cut out and stored.
const batchDuration = new Trend('batch_duration', true);
// Batch request -> this hotspot's segmentation seen as done (resolution: POLL_S). Measured on the laptop's clock only,
// so clock skew to the server does not matter.
const segmentationLatency = new Trend('segmentation_latency', true);
// Batch request -> the last hotspot segmented.
const drainDuration = new Trend('segmentation_drain', true);

export const options = {
  scenarios: { gpu: { executor: 'shared-iterations', iterations: 1, vus: 1, maxDuration: '100m' } },
  thresholds: { batch_duration: [], segmentation_latency: [], segmentation_drain: [], http_req_failed: ['rate<0.01'] },
  systemTags: SYSTEM_TAGS,
  summaryTrendStats: SUMMARY_TREND_STATS,
  setupTimeout: '20m',
};

export function setup() {
  requireEnv('K6_USER_PASSWORD');
  const client = new Client(MANAGER, USER_PASSWORD);
  client.login();
  const folder = client.must(
    client.post('/hotspot-folders', { name: `${GPU_FOLDER_PREFIX}${Date.now()}` }, 'POST /hotspot-folders', 'setup'),
    'create folder',
  );
  return { slide: largestSlide(readSlides(client)), folderId: folder.id };
}

export default function (data) {
  orAbort(() => work(data));
}

function work({ slide, folderId }) {
  const client = new Client(MANAGER, USER_PASSWORD);
  client.login();
  const boxes = boxGrid(slide).sort(() => Math.random() - 0.5).slice(0, HOTSPOTS);
  if (boxes.length < HOTSPOTS) fail(`Only ${boxes.length} boxes fit in the tissue of ${slide.name}`);

  const started = Date.now();
  let batch = client.must(
    client.post(
      '/hotspot-batches',
      { id: uuid(), slide_id: slide.id, folder_id: folderId, name_prefix: 'k6-gpu', boxes },
      'POST /hotspot-batches',
    ),
    'create batch',
  );
  while (batch.status === 'running') {
    sleep(POLL_S);
    batch = client.must(client.get(`/hotspot-batches/${batch.id}`, 'GET /hotspot-batches/{id}'), 'batch');
  }
  if (batch.status !== 'done') fail(`batch ${batch.status}: ${batch.error_message}`);
  batchDuration.add(Date.now() - started);

  const finished = new Set();
  let progressAt = Date.now();
  for (;;) {
    const hotspots = client.must(client.get(`/hotspots?folder_id=${folderId}`, 'GET /hotspots?folder_id'), 'hotspots');
    for (const hotspot of hotspots) {
      if (finished.has(hotspot.id) || hotspot.segmentation_status === 'pending') continue;
      finished.add(hotspot.id);
      progressAt = Date.now();
      if (hotspot.segmentation_status === 'done') segmentationLatency.add(Date.now() - started);
      else console.warn(`hotspot ${hotspot.id}: ${hotspot.segmentation_status} ${hotspot.segmentation_error}`);
    }
    console.log(`segmented ${finished.size}/${hotspots.length}`);
    if (finished.size === hotspots.length) break;
    if (Date.now() - progressAt > STALL_MS || Date.now() - started > TIMEOUT_MS) {
      fail(`${hotspots.length - finished.size} hotspots still pending: is the GPU worker running?`);
    }
    sleep(POLL_S);
  }
  drainDuration.add(Date.now() - started);
}

export function teardown({ folderId }) {
  if (__ENV.KEEP) return;
  const client = new Client(MANAGER, USER_PASSWORD);
  client.login();
  client.must(client.del(`/hotspot-folders/${folderId}`, 'DELETE /hotspot-folders/{id}', 'setup'), 'delete folder');
}
