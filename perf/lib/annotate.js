// An expert's own layer on a hotspot: start it (a copy of the model's cells), edit it one save at a time, confirm it.
// Creates and deletes are kept in balance, so a hotspot's cell count, and with it the agreement cost, stays the same
// from run to run.

import { uuid, randomInt, randomItem } from './api.js';

const NUDGE_PX = 3;

/** Makes the layer editable: started, and not confirmed (a confirmed layer is read-only). */
export function openLayer(client, hotspot) {
  const agreement = client.json(
    client.get(`/hotspots/${hotspot.id}/agreement`, 'GET /hotspots/{id}/agreement'),
    'agreement',
  );
  if (!agreement || !agreement.my_status) {
    client.json(
      client.post(`/hotspots/${hotspot.id}/reannotation`, undefined, 'POST /hotspots/{id}/reannotation', 'save'),
      'start reannotation',
    );
  } else if (agreement.my_status === 'confirmed') {
    client.json(
      client.del(`/hotspots/${hotspot.id}/reannotation/confirmation`, 'DELETE /hotspots/{id}/reannotation/confirmation', 'agreement'),
      'unconfirm',
    );
  }
  const annotations = client.json(
    client.get(`/hotspots/${hotspot.id}/annotations`, 'GET /hotspots/{id}/annotations'),
    'annotations',
  ) || [];
  const own = annotations.filter((item) => item.kind === 'cell' && item.created_by_name === client.username);
  return { hotspot, cells: own.map((item) => ({ id: item.id, ring: item.geometry.coordinates[0] })), created: [] };
}

/** One save, like a single edit in the viewer: mostly reshaping a cell, sometimes adding or removing one. */
export function editOnce(client, layer, cellTypeIds) {
  const roll = Math.random();
  if (layer.created.length && roll < 0.15) return deleteCell(client, layer);
  if (roll < 0.3 || !layer.cells.length) return addCell(client, layer, cellTypeIds);
  return reshapeCell(client, layer);
}

/** Removes the cells this session added (so the count is back where it started) and confirms the layer. */
export function closeLayer(client, layer) {
  while (layer.created.length) deleteCell(client, layer);
  client.json(
    client.put(`/hotspots/${layer.hotspot.id}/reannotation/confirmation`, undefined, 'PUT /hotspots/{id}/reannotation/confirmation', 'agreement'),
    'confirm',
  );
}

function reshapeCell(client, layer) {
  const cell = randomItem(layer.cells);
  const { width, height } = layer.hotspot;
  const dx = randomInt(-NUDGE_PX, NUDGE_PX);
  const dy = randomInt(-NUDGE_PX, NUDGE_PX);
  const ring = cell.ring.map(([x, y]) => [Math.min(Math.max(x + dx, 0), width), Math.min(Math.max(y + dy, 0), height)]);
  const saved = client.json(
    client.patch(
      `/hotspots/${layer.hotspot.id}/annotations/${cell.id}`,
      { geometry: { type: 'Polygon', coordinates: [ring] } },
      'PATCH /hotspots/{id}/annotations/{aid}',
      'save',
    ),
    'reshape cell',
  );
  if (saved) cell.ring = saved.geometry.coordinates[0];
}

function addCell(client, layer, cellTypeIds) {
  const { width, height } = layer.hotspot;
  const radius = randomInt(6, 12);
  const cx = randomInt(radius + 2, width - radius - 2);
  const cy = randomInt(radius + 2, height - radius - 2);
  const ring = Array.from({ length: 8 }, (_, index) => {
    const angle = (index / 8) * 2 * Math.PI;
    return [Math.round(cx + radius * Math.cos(angle)), Math.round(cy + radius * Math.sin(angle))];
  });
  const id = uuid();
  const saved = client.json(
    client.post(
      `/hotspots/${layer.hotspot.id}/annotations`,
      { id, kind: 'cell', cell_type_id: randomItem(cellTypeIds), geometry: { type: 'Polygon', coordinates: [ring] } },
      'POST /hotspots/{id}/annotations',
      'save',
    ),
    'add cell',
    [201],
  );
  if (saved) {
    layer.created.push(id);
    layer.cells.push({ id, ring: saved.geometry.coordinates[0] });
  }
}

function deleteCell(client, layer) {
  const id = layer.created.pop();
  client.json(
    client.del(`/hotspots/${layer.hotspot.id}/annotations/${id}`, 'DELETE /hotspots/{id}/annotations/{aid}', 'save'),
    'delete cell',
    [204],
  );
  layer.cells = layer.cells.filter((cell) => cell.id !== id);
}
