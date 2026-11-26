// One-off, idempotent seeding of the test data in the deployed app (re-run it to top anything up):
//   - organization k6-perf with k6-manager (ORG_ADMIN) and k6-expert-001..130 (ANNOTATOR), profiles set up;
//   - the test slides, imported from files already in the bucket (SLIDE_KEYS, see README);
//   - folder k6-pool with one segmented hotspot per team of three (needs the GPU worker running).
//   k6 run -e SLIDE_KEYS=perf/CMU-1.svs,perf/CMU-2.svs,perf/Mirax2.2-1/ setup/seed.js

import { fail, sleep } from 'k6';
import { Client, orAbort, uuid } from '../lib/api.js';
import {
  EXPERT_COUNT,
  MANAGER,
  ORGANIZATION,
  POOL_FOLDER,
  POOL_SIZE,
  USER_PASSWORD,
  expertName,
  requireEnv,
} from '../lib/config.js';
import { boxGrid, largestSlide, readSlides } from '../lib/data.js';

const POLL_S = 10;
const TIMEOUT_MS = 2 * 60 * 60 * 1000;

export const options = {
  scenarios: { seed: { executor: 'shared-iterations', iterations: 1, vus: 1, maxDuration: '150m' } },
};

export default function () {
  orAbort(() => work());
}

function work() {
  requireEnv('K6_ADMIN_USER', 'K6_ADMIN_PASSWORD', 'K6_USER_PASSWORD');
  const admin = new Client(__ENV.K6_ADMIN_USER, __ENV.K6_ADMIN_PASSWORD);
  admin.login();

  const organization = ensureOrganization(admin);
  ensureAccounts(admin, organization.id);
  importSlides(admin, organization.id);

  const manager = new Client(MANAGER, USER_PASSWORD);
  manager.login();
  ensurePool(manager);
  console.log('Seed complete.');
}

function ensureOrganization(admin) {
  const organizations = admin.must(admin.get('/organizations/', 'GET /organizations/', 'admin'), 'organizations');
  const existing = organizations.find((item) => item.name === ORGANIZATION);
  if (existing) return existing;
  return admin.must(admin.post('/organizations/', { name: ORGANIZATION }, 'POST /organizations/', 'admin'), 'create organization');
}

function ensureAccounts(admin, organizationId) {
  const accounts = admin.must(admin.get('/users/', 'GET /users/', 'admin'), 'accounts');
  const byName = Object.fromEntries(accounts.map((account) => [account.username, account]));
  const wanted = [[MANAGER, 'ORG_ADMIN']];
  for (let number = 1; number <= EXPERT_COUNT; number++) wanted.push([expertName(number), 'ANNOTATOR']);

  for (const [username, role] of wanted) {
    if (!byName[username]) {
      byName[username] = admin.must(
        admin.post(
          '/users/',
          { username, password: USER_PASSWORD, role, organization_id: organizationId },
          'POST /users/',
          'admin',
        ),
        `create ${username}`,
      );
    }
    if (byName[username].is_first_login) {
      // The first login only allows setting up the profile; the password stays the same.
      const user = new Client(username, USER_PASSWORD);
      user.login();
      user.must(
        user.post(
          '/auth/setup-profile',
          { new_password: USER_PASSWORD, first_name: 'k6', last_name: username },
          'POST /auth/setup-profile',
          'admin',
        ),
        `profile of ${username}`,
      );
    }
  }
  console.log(`${wanted.length} accounts ready.`);
}

function importSlides(admin, organizationId) {
  const keys = (__ENV.SLIDE_KEYS || '').split(',').map((key) => key.trim()).filter(Boolean);
  const ids = keys.map(
    (key) =>
      admin.must(
        admin.post('/upload/import-from-storage', { key, organization_id: organizationId }, 'POST /upload/import-from-storage', 'admin'),
        `import ${key}`,
      ).slide_id,
  );
  const started = Date.now();
  for (const id of ids) {
    for (;;) {
      const slide = admin.must(admin.get(`/slides/${id}`, 'GET /slides/{id}', 'admin'), 'slide');
      if (slide.status === 'ready') break;
      if (slide.status === 'failed') fail(`slide ${slide.name}: ${slide.error_message}`);
      if (Date.now() - started > TIMEOUT_MS) fail(`slide ${slide.name} still ${slide.status}`);
      console.log(`slide ${slide.name}: ${slide.status} ${slide.ingest_progress || 0}%`);
      sleep(POLL_S);
    }
  }
  console.log(`${ids.length} slides ready.`);
}

function ensurePool(manager) {
  const folders = manager.must(manager.get('/hotspot-folders', 'GET /hotspot-folders', 'admin'), 'folders');
  const folder =
    folders.find((item) => item.name === POOL_FOLDER) ||
    manager.must(manager.post('/hotspot-folders', { name: POOL_FOLDER }, 'POST /hotspot-folders', 'admin'), 'create pool');
  const listPool = () =>
    manager.must(manager.get(`/hotspots?folder_id=${folder.id}`, 'GET /hotspots?folder_id', 'admin'), 'pool');

  // A hotspot the model failed on can't be reannotated: replace it.
  for (const hotspot of listPool().filter((item) => item.segmentation_status === 'failed')) {
    console.warn(`Replacing ${hotspot.name}: ${hotspot.segmentation_error}`);
    manager.must(manager.del(`/hotspots/${hotspot.id}`, 'DELETE /hotspots/{id}', 'admin'), 'delete failed hotspot');
  }

  const existing = listPool();
  if (existing.length < POOL_SIZE) {
    const slide = largestSlide(readSlides(manager));
    const taken = new Set(existing.map((item) => `${item.x},${item.y}`));
    const boxes = boxGrid(slide)
      .filter(([x, y]) => !taken.has(`${x},${y}`))
      .slice(0, POOL_SIZE - existing.length);
    if (boxes.length < POOL_SIZE - existing.length) fail(`Not enough tissue on ${slide.name} for ${POOL_SIZE} hotspots`);
    let batch = manager.must(
      manager.post(
        '/hotspot-batches',
        { id: uuid(), slide_id: slide.id, folder_id: folder.id, name_prefix: POOL_FOLDER, boxes },
        'POST /hotspot-batches',
        'admin',
      ),
      'create pool batch',
    );
    while (batch.status === 'running') {
      sleep(POLL_S);
      batch = manager.must(manager.get(`/hotspot-batches/${batch.id}`, 'GET /hotspot-batches/{id}', 'admin'), 'batch');
    }
    if (batch.status !== 'done') fail(`pool batch ${batch.status}: ${batch.error_message}`);
  }

  // No hotspot segmented for this long: the GPU worker is not running (the first job also loads the model).
  const stallMs = 15 * 60 * 1000;
  let progressAt = Date.now();
  let lastDone = -1;
  for (;;) {
    const pool = listPool();
    const done = pool.filter((item) => item.segmentation_status === 'done').length;
    const pending = pool.filter((item) => item.segmentation_status === 'pending').length;
    console.log(`pool: ${done} segmented, ${pending} pending, ${pool.length - done - pending} failed`);
    if (!pending) {
      if (done < POOL_SIZE) fail('Some pool hotspots failed segmentation: run the seed again to replace them.');
      return;
    }
    if (done !== lastDone) {
      lastDone = done;
      progressAt = Date.now();
    }
    if (Date.now() - progressAt > stallMs) fail('Pool hotspots still pending: is the GPU worker running?');
    sleep(POLL_S);
  }
}
