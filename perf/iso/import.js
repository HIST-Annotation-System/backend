// A slide import alone: the system admin registers a slide file already in the bucket, and the EC2 host downloads and
// processes it. Deleting the slide afterwards also deletes its file, so import a throw-away copy whose name starts with
// k6-import- (see README):
//   k6 run -e IMPORT_KEY=perf/import/k6-import-CMU-1.svs iso/import.js

import { fail, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { Client, orAbort } from '../lib/api.js';
import { IMPORT_SLIDE_PREFIX, ORGANIZATION, SUMMARY_TREND_STATS, SYSTEM_TAGS, requireEnv } from '../lib/config.js';

const POLL_S = 2;
const TIMEOUT_MS = 60 * 60 * 1000;

// Import request -> the slide is ready (downloaded, opened, thumbnail and tissue box done).
const importDuration = new Trend('import_duration', true);

export const options = {
  scenarios: { import: { executor: 'shared-iterations', iterations: 1, vus: 1, maxDuration: '70m' } },
  thresholds: { import_duration: [], http_req_failed: ['rate<0.01'] },
  systemTags: SYSTEM_TAGS,
  summaryTrendStats: SUMMARY_TREND_STATS,
};

function admin() {
  requireEnv('K6_ADMIN_USER', 'K6_ADMIN_PASSWORD', 'IMPORT_KEY');
  const client = new Client(__ENV.K6_ADMIN_USER, __ENV.K6_ADMIN_PASSWORD);
  client.login();
  return client;
}

export function setup() {
  const file = __ENV.IMPORT_KEY.split('/').filter(Boolean).pop();
  if (!file.startsWith(IMPORT_SLIDE_PREFIX)) fail(`IMPORT_KEY must name a copy called ${IMPORT_SLIDE_PREFIX}*`);
  return {};
}

export default function () {
  orAbort(() => work());
}

function work() {
  const client = admin();
  const organizations = client.must(client.get('/organizations/', 'GET /organizations/', 'setup'), 'organizations');
  const organization = organizations.find((item) => item.name === ORGANIZATION);
  if (!organization) fail(`Organization ${ORGANIZATION} is missing: run setup/seed.js first.`);

  const started = Date.now();
  const { slide_id: slideId, created } = client.must(
    client.post(
      '/upload/import-from-storage',
      { key: __ENV.IMPORT_KEY, organization_id: organization.id },
      'POST /upload/import-from-storage',
    ),
    'import',
  );
  if (!created) fail(`${__ENV.IMPORT_KEY} was imported before: copy the file to a new key.`);
  for (;;) {
    const slide = client.must(client.get(`/slides/${slideId}`, 'GET /slides/{id}'), 'slide');
    console.log(`${slide.status} ${slide.ingest_progress ?? ''}`);
    if (slide.status === 'ready') break;
    if (slide.status === 'failed') fail(`import failed: ${slide.error_message}`);
    if (Date.now() - started > TIMEOUT_MS) fail('import still processing after an hour');
    sleep(POLL_S);
  }
  importDuration.add(Date.now() - started);
}

export function teardown() {
  if (__ENV.KEEP) return;
  const client = admin();
  const slides = client.must(client.get('/slides', 'GET /slides', 'setup'), 'slides');
  const name = __ENV.IMPORT_KEY.split('/').filter(Boolean).pop();
  for (const slide of slides.filter((item) => item.original_filename === name)) {
    client.must(client.del(`/slides/${slide.id}`, 'DELETE /slides/{id}', 'setup'), 'delete slide');
  }
}
