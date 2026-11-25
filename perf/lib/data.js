import { fail, sleep } from 'k6';
import { Client } from './api.js';
import { IMPORT_SLIDE_PREFIX, POOL_FOLDER, POOL_SIZE, USER_PASSWORD, expertName, requireEnv } from './config.js';
import { parseDzi } from './viewport.js';

const WARM_UP_TIMEOUT_MS = 15 * 60 * 1000;
const SLIDE_PREPARING_RETRY_S = 5;

/**
 * GET image.dzi until the slide is in the host's cache (503 `slide_preparing` while it downloads from S3), as the
 * frontend does. Returns the DZI text.
 */
export function openDzi(client, slideId, kind = 'api') {
  const deadline = Date.now() + WARM_UP_TIMEOUT_MS;
  for (;;) {
    const res = client.get(`/slides/${slideId}/image.dzi`, 'GET /slides/{id}/image.dzi', kind);
    if (res.status === 200) return res.body;
    if (res.status !== 503 || Date.now() > deadline) fail(`image.dzi of ${slideId}: ${res.status} ${res.body}`);
    sleep(SLIDE_PREPARING_RETRY_S);
  }
}

const BOX_SIZE = 1024;

/** Top-left corners (level-0 slide pixels) of the 1024 px squares that tile the slide's tissue, row by row. */
export function boxGrid(slide) {
  const { x, y, width, height } = slide.tissueSlide;
  const corners = [];
  for (let top = y; top + BOX_SIZE <= y + height; top += BOX_SIZE) {
    for (let left = x; left + BOX_SIZE <= x + width; left += BOX_SIZE) corners.push([left, top]);
  }
  return corners;
}

/** The slide with the most tissue: hotspots are cut out of it. */
export function largestSlide(slides) {
  return slides.reduce((best, slide) => (boxGrid(slide).length > boxGrid(best).length ? slide : best));
}

/**
 * The ready test slides (import copies left out), each opened once: that is the warm-up. A member sees only their
 * organization's slides; the system admin passes `organizationId`.
 */
export function readSlides(client, organizationId = null) {
  const listed = client.must(client.get('/slides', 'GET /slides', 'setup'), 'list slides');
  const ready = listed.filter(
    (slide) =>
      slide.status === 'ready' &&
      !slide.name.startsWith(IMPORT_SLIDE_PREFIX) &&
      (organizationId === null || slide.organization_id === organizationId),
  );
  if (!ready.length) fail('No ready slides in the k6-perf organization: run setup/seed.js first.');
  return ready.map((slide) => {
    const offset = slide.image_offset || { x: 0, y: 0 };
    const box = slide.tissue_bbox || { x: offset.x, y: offset.y, width: slide.width, height: slide.height };
    return {
      id: slide.id,
      name: slide.name,
      dzi: parseDzi(openDzi(client, slide.id, 'setup')),
      // Deep Zoom image pixels, for tiles.
      tissue: { x: box.x - offset.x, y: box.y - offset.y, width: box.width, height: box.height },
      // Level-0 slide pixels, for hotspot boxes.
      tissueSlide: box,
    };
  });
}

/**
 * What the load scripts need, read as the first test expert (seed it first with setup/seed.js). Opening every slide
 * here is also the warm-up: the measured part never waits for a slide to be downloaded to the host.
 */
export function loadTestData() {
  requireEnv('K6_USER_PASSWORD');
  const client = new Client(expertName(1), USER_PASSWORD);
  client.login();

  const slides = readSlides(client);

  const folders = client.must(client.get('/hotspot-folders', 'GET /hotspot-folders', 'setup'), 'list folders');
  const poolFolder = folders.find((folder) => folder.name === POOL_FOLDER);
  if (!poolFolder) fail(`Folder ${POOL_FOLDER} is missing: run setup/seed.js first.`);
  const hotspots = client.must(
    client.get(`/hotspots?folder_id=${poolFolder.id}&sort=created_at&order=asc`, 'GET /hotspots', 'setup'),
    'list pool hotspots',
  );
  const pool = hotspots
    .filter((hotspot) => hotspot.segmentation_status === 'done')
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((hotspot) => ({ id: hotspot.id, width: hotspot.image_width, height: hotspot.image_height }));
  if (pool.length < POOL_SIZE) {
    fail(`${pool.length}/${POOL_SIZE} pool hotspots are segmented: run setup/seed.js with the GPU worker up.`);
  }

  const cellTypes = client.must(client.get('/cell-types/', 'GET /cell-types/', 'setup'), 'list cell types');
  return { slides, pool, cellTypeIds: cellTypes.map((type) => type.id) };
}
