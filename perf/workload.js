// The realistic mix, the same for every load shape: protocol experts make the load, 3 browser VUs measure what a user
// sees meanwhile. Only the shape changes:  k6 run -e PROFILE=smoke|average|stress|spike|soak workload.js

import { expertSession } from './journeys/expert.js';
import { browserJourney } from './journeys/browser.js';
import { loadTestData } from './lib/data.js';
import { SUMMARY_TREND_STATS, SYSTEM_TAGS } from './lib/config.js';

const BROWSER_VUS = 3;

const step = (target, hold = '4m') => [{ duration: '1m', target }, { duration: hold, target }];

// Expert VUs over time. `stress` climbs the presented chart (5 -> 60) and keeps going until a threshold aborts it.
const PROFILES = {
  smoke: null,
  average: [{ duration: '2m', target: 15 }, { duration: '15m', target: 15 }, { duration: '1m', target: 0 }],
  stress: [5, 15, 30, 45, 60, 75, 90, 105, 120].flatMap((target) => step(target)),
  spike: [
    { duration: '30s', target: 60 },
    { duration: '3m', target: 60 },
    { duration: '30s', target: 0 },
    // Nobody but the browser VUs: does the app recover?
    { duration: '5m', target: 0 },
  ],
  soak: [{ duration: '2m', target: 15 }, { duration: '2h', target: 15 }, { duration: '1m', target: 0 }],
};

const profile = __ENV.PROFILE || 'smoke';
if (!(profile in PROFILES)) throw new Error(`PROFILE must be one of ${Object.keys(PROFILES).join(', ')}`);
const stages = PROFILES[profile];
const duration = stages && stages.reduce((total, stage) => total + seconds(stage.duration), 0);

function seconds(text) {
  const units = { s: 1, m: 60, h: 3600 };
  return [...text.matchAll(/(\d+)([smh])/g)].reduce((total, [, value, unit]) => total + Number(value) * units[unit], 0);
}

// Breaking points end stress runs early; elsewhere a failed threshold only marks the run as failed.
const abort = (threshold) => (profile === 'stress' ? [{ threshold, abortOnFail: true, delayAbortEval: '1m' }] : []);

export const options = {
  scenarios: {
    experts: stages
      ? { executor: 'ramping-vus', exec: 'experts', startVUs: 0, stages, gracefulRampDown: '30s' }
      : { executor: 'per-vu-iterations', exec: 'experts', vus: 1, iterations: 1, maxDuration: '15m' },
    browser: stages
      ? { executor: 'constant-vus', exec: 'browserUsers', vus: BROWSER_VUS, duration: `${duration}s`, options: { browser: { type: 'chromium' } } }
      : { executor: 'per-vu-iterations', exec: 'browserUsers', vus: 1, iterations: 1, maxDuration: '5m', options: { browser: { type: 'chromium' } } },
  },
  thresholds: {
    'http_req_duration{scenario:experts,kind:api}': ['p(95)<500'],
    'http_req_duration{scenario:experts,kind:tile}': ['p(95)<1000', ...abort('p(95)<3000')],
    'http_req_duration{scenario:experts,kind:save}': ['p(95)<500'],
    'http_req_failed{scenario:experts}': ['rate<0.01', ...abort('rate<0.05')],
    // Recorded with their own series, no limit: they depend on the slide and the hotspot more than on the load.
    'http_req_duration{scenario:experts,kind:agreement}': [],
    'http_req_duration{scenario:experts,kind:features}': [],
    'http_req_duration{scenario:experts,kind:image}': [],
    'viewer_ready{scenario:browser}': ['p(95)<3000'],
    'hotspot_ready{scenario:browser}': [],
    'browser_web_vital_lcp{scenario:browser}': ['p(95)<2500'],
  },
  // A browser loads a screen of tiles at once (HTTP/2 to CloudFront, no per-host limit in OpenSeadragon).
  batch: 40,
  batchPerHost: 0,
  systemTags: SYSTEM_TAGS,
  summaryTrendStats: SUMMARY_TREND_STATS,
  setupTimeout: '20m',
};

export function setup() {
  return loadTestData();
}

export function experts(data) {
  expertSession(data);
}

export async function browserUsers(data) {
  await browserJourney(data);
}
