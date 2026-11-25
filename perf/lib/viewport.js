// What an OpenSeadragon viewer of SCREEN size asks for while a user pans and zooms: the Deep Zoom tiles covering the
// view at the current level (~30 tiles for 1600x900 and 254 px tiles).

import { SCREEN } from './config.js';
import { randomItem } from './api.js';

export function parseDzi(xml) {
  const number = (attribute) => Number(new RegExp(`${attribute}="(\\d+)"`).exec(xml)[1]);
  return { tileSize: number('TileSize'), width: number('Width'), height: number('Height') };
}

/** `slide` as `loadTestData` returns it: Deep Zoom size plus the tissue box in Deep Zoom image pixels. */
export class Viewport {
  constructor(slide, screen = SCREEN) {
    this.dzi = slide.dzi;
    this.screen = screen;
    this.tissue = slide.tissue;
    this.maxLevel = Math.ceil(Math.log2(Math.max(this.dzi.width, this.dzi.height)));
    // The viewer opens zoomed onto the tissue (frontend: useTissueFocus).
    const fit = Math.max(this.tissue.width / screen.width, this.tissue.height / screen.height, 1);
    this.fitLevel = Math.max(0, this.maxLevel - Math.ceil(Math.log2(fit)));
    this.level = this.fitLevel;
    this.x = this.tissue.x + this.tissue.width / 2;
    this.y = this.tissue.y + this.tissue.height / 2;
  }

  /** Level-0 pixels per pixel of the current level. */
  get factor() {
    return 2 ** (this.maxLevel - this.level);
  }

  /** Tile paths (`level/col_row`) covering the view. */
  tiles() {
    const { tileSize, width, height } = this.dzi;
    const f = this.factor;
    const lastCol = Math.ceil(Math.ceil(width / f) / tileSize) - 1;
    const lastRow = Math.ceil(Math.ceil(height / f) / tileSize) - 1;
    const clamp = (value, last) => Math.min(Math.max(value, 0), last);
    const left = clamp(Math.floor((this.x / f - this.screen.width / 2) / tileSize), lastCol);
    const right = clamp(Math.floor((this.x / f + this.screen.width / 2) / tileSize), lastCol);
    const top = clamp(Math.floor((this.y / f - this.screen.height / 2) / tileSize), lastRow);
    const bottom = clamp(Math.floor((this.y / f + this.screen.height / 2) / tileSize), lastRow);
    const paths = [];
    for (let row = top; row <= bottom; row++) {
      for (let col = left; col <= right; col++) paths.push(`${this.level}/${col}_${row}`);
    }
    return paths;
  }

  /** One user gesture: zoom in towards a point in view, zoom out, or drag the view, staying over the tissue. */
  move() {
    const f = this.factor;
    const gestures = ['pan', 'pan'];
    if (this.level < this.maxLevel) gestures.push('in', 'in');
    if (this.level > this.fitLevel) gestures.push('out');
    const gesture = randomItem(gestures);
    if (gesture === 'in') {
      this.x += (Math.random() - 0.5) * this.screen.width * f * 0.5;
      this.y += (Math.random() - 0.5) * this.screen.height * f * 0.5;
      this.level += 1;
    } else if (gesture === 'out') {
      this.level -= 1;
    } else {
      this.x += (Math.random() - 0.5) * this.screen.width * f * 1.2;
      this.y += (Math.random() - 0.5) * this.screen.height * f * 1.2;
    }
    const { x, y, width, height } = this.tissue;
    this.x = Math.min(Math.max(this.x, x), x + width);
    this.y = Math.min(Math.max(this.y, y), y + height);
    return gesture;
  }

  /** A random point deep in the tissue: for tile-only tests that should not depend on a pan/zoom history. */
  jump() {
    const { x, y, width, height } = this.tissue;
    this.x = x + Math.random() * width;
    this.y = y + Math.random() * height;
    this.level = this.fitLevel + Math.floor(Math.random() * (this.maxLevel - this.fitLevel + 1));
  }
}
