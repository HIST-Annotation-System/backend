// Settings shared by every script. Everything that changes between runs comes from the environment (`-e NAME=value`).

export const BASE_URL = (__ENV.BASE_URL || 'https://d3s9lf5kpej9we.cloudfront.net').replace(/\/$/, '');
export const API = `${BASE_URL}/api/v1`;

export const ORGANIZATION = 'k6-perf';
export const POOL_FOLDER = 'k6-pool';
export const GPU_FOLDER_PREFIX = 'k6-gpu-';
export const IMPORT_SLIDE_PREFIX = 'k6-import-';
export const MANAGER = 'k6-manager';

// Every VU signs in as `k6-expert-<its VU number>`, so no two VUs share an account. VU numbers are counted over all
// scenarios of a run: 120 experts + 3 browser VUs fit, with a little headroom.
export const EXPERT_COUNT = 130;
export const TEAM_SIZE = 3;
// One hotspot per team of three experts.
export const POOL_SIZE = Math.ceil(EXPERT_COUNT / TEAM_SIZE);

export const expertName = (number) => `k6-expert-${String(number).padStart(3, '0')}`;

export const USER_PASSWORD = __ENV.K6_USER_PASSWORD;

// The browser window the protocol VUs pretend to have (OpenSeadragon loads the tiles that cover it).
export const SCREEN = { width: 1600, height: 900 };

// Only these system tags become metric labels: `url` would create one Prometheus series per tile.
export const SYSTEM_TAGS = ['proto', 'status', 'method', 'name', 'group', 'check', 'error_code', 'scenario', 'expected_response'];
export const SUMMARY_TREND_STATS = ['avg', 'med', 'p(95)', 'p(99)', 'max'];

export function requireEnv(...names) {
  const missing = names.filter((name) => !__ENV[name]);
  if (missing.length) throw new Error(`Set ${missing.join(', ')} (k6 run -e NAME=value ...)`);
}
