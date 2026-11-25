// Annotation work alone, in two phases:
//   saves     - cell edits at a rising rate (each VU edits its own layer on its team's pool hotspot);
//   confirms  - 1 -> 5 teams of three confirming and withdrawing at the same time; every confirmation recomputes the
//               hotspot's agreement (the CPU-heavy part, see backend/benchmarks).
//   k6 run -e SAVES_MAX_RATE=30 iso/annotation.js

import exec from 'k6/execution';
import { sleep } from 'k6';
import { Client } from '../lib/api.js';
import { EXPERT_COUNT, SUMMARY_TREND_STATS, SYSTEM_TAGS, TEAM_SIZE, USER_PASSWORD, expertName } from '../lib/config.js';
import { loadTestData } from '../lib/data.js';
import { editOnce, openLayer } from '../lib/annotate.js';
import { teamHotspot } from '../journeys/expert.js';

const MAX_RATE = Number(__ENV.SAVES_MAX_RATE || 30);
const SAVE_VUS = 60;
const CONFIRM_TEAMS = 5;

const hold = (target) => [{ duration: '1m', target }, { duration: '2m', target }];

export const options = {
  scenarios: {
    saves: {
      executor: 'ramping-arrival-rate',
      exec: 'save',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: SAVE_VUS,
      stages: [{ duration: '10m', target: MAX_RATE }],
    },
    confirms: {
      executor: 'ramping-vus',
      exec: 'confirm',
      startTime: '10m30s',
      startVUs: 0,
      stages: [1, 2, 3, 4, 5].flatMap(hold),
    },
  },
  thresholds: {
    'http_req_duration{scenario:saves,kind:save}': ['p(95)<500', { threshold: 'p(95)<3000', abortOnFail: true, delayAbortEval: '1m' }],
    'http_req_failed{scenario:saves}': ['rate<0.01', { threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '1m' }],
    'http_req_duration{scenario:confirms,kind:agreement}': [],
    'http_req_failed{scenario:confirms}': ['rate<0.01'],
  },
  systemTags: SYSTEM_TAGS,
  summaryTrendStats: SUMMARY_TREND_STATS,
  setupTimeout: '20m',
};

export function setup() {
  const data = loadTestData();
  // Save VUs are numbered 1..SAVE_VUS; signing them in here keeps bcrypt logins out of the measured ramp.
  data.tokens = Array.from({ length: SAVE_VUS + CONFIRM_TEAMS }, (_, index) =>
    new Client(expertName(index + 1), USER_PASSWORD).login().access_token,
  );
  return data;
}

let client = null;
let layer = null;
let team = null;

export function save(data) {
  if (!client) {
    client = new Client(expertName(exec.vu.idInTest), USER_PASSWORD);
    client.token = data.tokens[exec.vu.idInTest - 1];
    client.loggedInAt = Date.now();
    layer = openLayer(client, teamHotspot(data));
  }
  editOnce(client, layer, data.cellTypeIds);
}

/**
 * One VU = one whole team (the last teams of the pool, whose accounts no save VU uses): all three confirm, then all
 * three withdraw. With two or more confirmed, each step recomputes the agreement.
 */
export function confirm(data) {
  if (!team) {
    // Whole teams only: the last one may be short of accounts.
    const index = Math.floor(EXPERT_COUNT / TEAM_SIZE) - 1 - (exec.vu.idInTest % CONFIRM_TEAMS);
    const hotspot = data.pool[index];
    team = Array.from({ length: TEAM_SIZE }, (_, member) => {
      const memberClient = new Client(expertName(index * TEAM_SIZE + member + 1), USER_PASSWORD);
      memberClient.login();
      return { client: memberClient, layer: openLayer(memberClient, hotspot) };
    });
  }
  for (const { client: member, layer: own } of team) {
    member.json(
      member.put(`/hotspots/${own.hotspot.id}/reannotation/confirmation`, undefined, 'PUT /hotspots/{id}/reannotation/confirmation', 'agreement'),
      'confirm',
    );
  }
  sleep(1);
  for (const { client: member, layer: own } of team) {
    member.json(
      member.del(`/hotspots/${own.hotspot.id}/reannotation/confirmation`, 'DELETE /hotspots/{id}/reannotation/confirmation', 'agreement'),
      'unconfirm',
    );
  }
  sleep(1);
}
