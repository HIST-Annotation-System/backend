// Removes test data from the deployed app.
//   k6 run setup/cleanup.js              left-overs of aborted runs: k6-gpu-* folders and k6-import-* slides
//   k6 run -e ALL=1 setup/cleanup.js     end of the test campaign: everything seed.js made, including the test
//                                        slides' files in the bucket, the accounts and the organization

import { Client, orAbort } from '../lib/api.js';
import {
  GPU_FOLDER_PREFIX,
  IMPORT_SLIDE_PREFIX,
  MANAGER,
  ORGANIZATION,
  POOL_FOLDER,
  USER_PASSWORD,
  requireEnv,
} from '../lib/config.js';

export const options = {
  scenarios: { cleanup: { executor: 'shared-iterations', iterations: 1, vus: 1, maxDuration: '30m' } },
};

const ALL = Boolean(__ENV.ALL);

export default function () {
  orAbort(() => work());
}

function work() {
  requireEnv('K6_USER_PASSWORD', ...(ALL ? ['K6_ADMIN_USER', 'K6_ADMIN_PASSWORD'] : []));
  const manager = new Client(MANAGER, USER_PASSWORD);
  manager.login();

  const folders = manager.must(manager.get('/hotspot-folders', 'GET /hotspot-folders', 'admin'), 'folders');
  for (const folder of folders.filter((item) => item.name.startsWith(GPU_FOLDER_PREFIX) || (ALL && item.name === POOL_FOLDER))) {
    // Its hotspots, their annotations and reannotations go with it (ondelete=CASCADE).
    manager.must(manager.del(`/hotspot-folders/${folder.id}`, 'DELETE /hotspot-folders/{id}', 'admin'), `delete ${folder.name}`);
    console.log(`Deleted folder ${folder.name}`);
  }

  // The manager sees only the k6-perf organization's slides.
  const slides = manager.must(manager.get('/slides', 'GET /slides', 'admin'), 'slides');
  for (const slide of slides.filter((item) => ALL || item.name.startsWith(IMPORT_SLIDE_PREFIX))) {
    manager.must(manager.del(`/slides/${slide.id}`, 'DELETE /slides/{id}', 'admin'), `delete ${slide.name}`);
    console.log(`Deleted slide ${slide.name}`);
  }

  if (!ALL) return;
  const admin = new Client(__ENV.K6_ADMIN_USER, __ENV.K6_ADMIN_PASSWORD);
  admin.login();
  const organizations = admin.must(admin.get('/organizations/', 'GET /organizations/', 'admin'), 'organizations');
  const organization = organizations.find((item) => item.name === ORGANIZATION);
  if (!organization) return;
  const accounts = admin.must(admin.get('/users/', 'GET /users/', 'admin'), 'accounts');
  for (const account of accounts.filter((item) => item.organization && item.organization.id === organization.id)) {
    admin.must(admin.del(`/users/${account.id}`, 'DELETE /users/{id}', 'admin'), `delete ${account.username}`);
  }
  admin.must(admin.del(`/organizations/${organization.id}`, 'DELETE /organizations/{id}', 'admin'), 'delete organization');
  console.log(`Deleted ${ORGANIZATION} and its accounts.`);
}
