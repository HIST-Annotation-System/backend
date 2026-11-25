// Tiles alone: random tiles of the test slides at a rising rate until the server gives up. Answers how many tiles per
// second one API process can render and what runs out first (CPU, slide cache disk, threads).
//   k6 run -e TILES_MAX_RATE=300 iso/tiles.js

import exec from 'k6/execution';
import { check } from 'k6';
import { Client, randomItem } from '../lib/api.js';
import { EXPERT_COUNT, SUMMARY_TREND_STATS, SYSTEM_TAGS, USER_PASSWORD, expertName } from '../lib/config.js';
import { loadTestData } from '../lib/data.js';
import { Viewport } from '../lib/viewport.js';

const MAX_RATE = Number(__ENV.TILES_MAX_RATE || 300);
// Signed in during setup, so no bcrypt login competes with the tiles being measured (the test is shorter than a
// token's 30 minutes).
const ACCOUNTS = 20;

export const options = {
  scenarios: {
    tiles: {
      executor: 'ramping-arrival-rate',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: Number(__ENV.TILES_MAX_VUS || 300),
      stages: [{ duration: '15m', target: MAX_RATE }],
    },
  },
  thresholds: {
    'http_req_duration{kind:tile}': ['p(95)<1000', { threshold: 'p(95)<3000', abortOnFail: true, delayAbortEval: '1m' }],
    'http_req_failed{kind:tile}': ['rate<0.01', { threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '1m' }],
  },
  systemTags: SYSTEM_TAGS,
  summaryTrendStats: SUMMARY_TREND_STATS,
  setupTimeout: '20m',
};

export function setup() {
  const data = loadTestData();
  data.tokens = Array.from({ length: Math.min(ACCOUNTS, EXPERT_COUNT) }, (_, index) => {
    const client = new Client(expertName(index + 1), USER_PASSWORD);
    return client.login().access_token;
  });
  return data;
}

let client = null;

export default function (data) {
  if (!client) {
    client = new Client(null, null);
    client.token = data.tokens[(exec.vu.idInTest - 1) % data.tokens.length];
  }
  const slide = randomItem(data.slides);
  const view = new Viewport(slide);
  view.jump();
  const path = randomItem(view.tiles());
  const res = client.get(
    `/slides/${slide.id}/image_files/${path}.jpeg`,
    'GET /slides/{id}/image_files/{level}/{col}_{row}.jpeg',
    'tile',
    { responseType: 'none' },
  );
  check(res, { 'tile: 200': (r) => r.status === 200 });
}
