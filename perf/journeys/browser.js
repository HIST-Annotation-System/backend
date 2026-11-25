// What a user sees in a real browser (Chromium via k6/browser) while the protocol VUs make the load: sign-in, the slide
// viewer showing its first tiles, and a hotspot with its cells. Reads only; the protocol VUs do the writing.
// Headless Chromium runs without WebGL, so OpenSeadragon draws with its canvas drawer: forcing WebGL through SwiftShader
// made every timing several times slower on the laptop's CPU, hiding the server's share.

import exec from 'k6/execution';
import { browser } from 'k6/browser';
import { Trend } from 'k6/metrics';
import { BASE_URL, SCREEN, USER_PASSWORD } from '../lib/config.js';
import { randomItem } from '../lib/api.js';
import { expertAccount, teamHotspot } from './expert.js';

// Navigation start -> the 10th tile has arrived (the first screen of the viewer is mostly drawn by then).
const viewerReady = new Trend('viewer_ready', true);
// Navigation start -> the hotspot's image and its cells have both arrived.
const hotspotReady = new Trend('hotspot_ready', true);

const FIRST_SCREEN_TILES = 10;
// The viewer's own canvas (the navigator minimap has one too).
const CANVAS = '.openseadragon-canvas[tabindex="0"]';
const READY_TIMEOUT_MS = 60 * 1000;

export async function browserJourney(data) {
  const context = await browser.newContext({ viewport: SCREEN });
  const page = await context.newPage();
  try {
    await page.goto(`${BASE_URL}/login`);
    await page.locator('#username').fill(expertAccount());
    await page.locator('#password').fill(USER_PASSWORD);
    await page.locator('button[type="submit"]').click();
    await page.getByRole('navigation', { name: 'Główna nawigacja' }).waitFor({ timeout: READY_TIMEOUT_MS });
    await page.waitForTimeout(3000);

    const slide = randomItem(data.slides);
    await page.goto(`${BASE_URL}/slides/${slide.id}`);
    await page.waitForFunction(
      (count) => performance.getEntriesByType('resource').filter((entry) => entry.name.includes('/image_files/')).length >= count,
      { polling: 100, timeout: READY_TIMEOUT_MS },
      FIRST_SCREEN_TILES,
    );
    viewerReady.add(
      await page.evaluate((count) => {
        const ends = performance.getEntriesByType('resource')
          .filter((entry) => entry.name.includes('/image_files/'))
          .map((entry) => entry.responseEnd)
          .sort((a, b) => a - b);
        return ends[count - 1];
      }, FIRST_SCREEN_TILES),
    );

    // A little zooming (mouse wheel) and dragging, so the browser keeps loading tiles like a user looking around.
    // k6/browser has no wheel input: the wheel event is dispatched in the page, where OpenSeadragon handles it.
    const canvas = page.locator(CANVAS);
    for (let step = 0; step < 3; step++) {
      await page.evaluate((selector) => {
        const element = document.querySelector(selector);
        const box = element.getBoundingClientRect();
        const at = { clientX: box.left + box.width / 2, clientY: box.top + box.height / 2 };
        element.dispatchEvent(new WheelEvent('wheel', { deltaY: -240, bubbles: true, cancelable: true, ...at }));
      }, CANVAS);
      await page.waitForTimeout(2000);
      const box = await canvas.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 - 200, box.y + box.height / 2 - 100, { steps: 10 });
      await page.mouse.up();
      await page.waitForTimeout(3000);
    }

    const hotspot = teamHotspot(data);
    await page.goto(`${BASE_URL}/hotspots/${hotspot.id}`);
    const loaded = (id) => {
      const entries = performance.getEntriesByType('resource');
      const image = entries.find((entry) => entry.name.includes(`/hotspots/${id}/image`));
      const cells = entries.find((entry) => entry.name.includes(`/hotspots/${id}/annotations`));
      return image && cells ? Math.max(image.responseEnd, cells.responseEnd) : 0;
    };
    await page.waitForFunction(loaded, { polling: 100, timeout: READY_TIMEOUT_MS }, hotspot.id);
    hotspotReady.add(await page.evaluate(loaded, hotspot.id));
    await page.waitForTimeout(5000);
  } catch (error) {
    // What the page showed when the journey failed (results/ is kept out of git).
    await page.screenshot({ path: `results/screenshots/${exec.vu.idInTest}-${Date.now()}.png` });
    throw error;
  } finally {
    await page.close();
    await context.close();
  }
}
