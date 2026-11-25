// One expert's work session over the API, the way the frontend sends it: the slide library, a slide viewed with pan
// and zoom, then the team's hotspot reannotated with a save every ~8 s. One iteration = one session (~4-7 min).

import http from 'k6/http';
import exec from 'k6/execution';
import { check, group, sleep } from 'k6';
import { Client, randomInt, randomItem } from '../lib/api.js';
import { API, TEAM_SIZE, USER_PASSWORD, expertName } from '../lib/config.js';
import { openDzi } from '../lib/data.js';
import { Viewport } from '../lib/viewport.js';
import { closeLayer, editOnce, openLayer } from '../lib/annotate.js';

// The frontend refreshes the slide list every 30 s while nothing is processing (useSlides).
const SLIDE_LIST_POLL_MS = 30 * 1000;
const TILE_NAME = 'GET /slides/{id}/image_files/{level}/{col}_{row}.jpeg';

// Per VU, kept across iterations like a browser tab would: the login, the browser cache of immutable images, the
// slide list's ETag.
let client = null;
const cached = new Set();
let slidesEtag = null;
let lastSlidesPoll = 0;

export function expertAccount() {
  return expertName(exec.vu.idInTest);
}

/** The pool hotspot this VU's team of three works on. */
export function teamHotspot(data) {
  return data.pool[Math.floor((exec.vu.idInTest - 1) / TEAM_SIZE) % data.pool.length];
}

export function expertSession(data) {
  if (!client) client = new Client(expertAccount(), USER_PASSWORD);
  client.ensureLogin();

  group('library', () => {
    client.json(client.get('/users/me', 'GET /users/me'), 'me');
    const slides = pollSlides(true);
    for (const slide of slides || data.slides) fetchImage(`/slides/${slide.id}/thumbnail`, 'GET /slides/{id}/thumbnail');
    think(5, 15);
  });

  group('slide viewer', () => {
    const slide = randomItem(data.slides);
    client.json(client.get(`/slides/${slide.id}`, 'GET /slides/{id}'), 'slide');
    openDzi(client, slide.id);
    client.json(client.get(`/hotspots?slide_id=${slide.id}`, 'GET /hotspots?slide_id'), 'slide hotspots');
    const view = new Viewport(slide);
    fetchTiles(slide.id, view.tiles());
    for (let moves = randomInt(6, 12); moves > 0; moves--) {
      think(3, 8);
      view.move();
      fetchTiles(slide.id, view.tiles());
      // Choosing squares to cut out shows their image descriptors live.
      if (Math.random() < 0.15) boxFeatures(slide);
    }
  });

  group('hotspot', () => {
    const hotspot = teamHotspot(data);
    client.json(client.get(`/hotspots/${hotspot.id}`, 'GET /hotspots/{id}'), 'hotspot');
    fetchImage(`/hotspots/${hotspot.id}/image`, 'GET /hotspots/{id}/image');
    client.json(client.get('/cell-types/', 'GET /cell-types/'), 'cell types');
    const layer = openLayer(client, hotspot);
    think(5, 10);
    for (let edits = randomInt(20, 40); edits > 0; edits--) {
      editOnce(client, layer, data.cellTypeIds);
      think(6, 10);
    }
    closeLayer(client, layer);
    think(3, 8);
  });
}

/** Sleeps like a user thinking; the open app keeps polling the slide list meanwhile. */
function think(min, max) {
  sleep(min + Math.random() * (max - min));
  pollSlides(false);
}

function pollSlides(force) {
  if (!force && Date.now() - lastSlidesPoll < SLIDE_LIST_POLL_MS) return null;
  lastSlidesPoll = Date.now();
  const headers = slidesEtag ? { 'If-None-Match': slidesEtag } : {};
  const res = client.get('/slides', 'GET /slides', 'api', { headers });
  check(res, { 'slides: 200/304': (r) => r.status === 200 || r.status === 304 });
  if (res.status !== 200) return null;
  slidesEtag = res.headers.Etag || res.headers.ETag || null;
  return res.json();
}

/** Images are `immutable`: a browser downloads each one once. */
function fetchImage(path, name) {
  if (cached.has(path)) return;
  const res = client.get(path, name, 'image', { responseType: 'none' });
  if (check(res, { [`${name}: 200`]: (r) => r.status === 200 })) cached.add(path);
}

function fetchTiles(slideId, paths) {
  const urls = paths.map((path) => `/slides/${slideId}/image_files/${path}.jpeg`).filter((url) => !cached.has(url));
  if (!urls.length) return;
  const params = client.params(TILE_NAME, 'tile', { responseType: 'none' });
  const responses = http.batch(urls.map((url) => ['GET', `${API}${url}`, null, params]));
  responses.forEach((res, index) => {
    if (check(res, { 'tile: 200': (r) => r.status === 200 })) cached.add(urls[index]);
  });
}

function boxFeatures(slide) {
  const { x, y, width, height } = slide.tissueSlide;
  const boxes = Array.from({ length: randomInt(1, 4) }, () => [
    Math.max(0, Math.round(x + Math.random() * Math.max(width - 1024, 0))),
    Math.max(0, Math.round(y + Math.random() * Math.max(height - 1024, 0))),
  ]);
  client.json(
    client.post('/hotspot-boxes/features', { slide_id: slide.id, boxes }, 'POST /hotspot-boxes/features', 'features'),
    'box features',
  );
}
