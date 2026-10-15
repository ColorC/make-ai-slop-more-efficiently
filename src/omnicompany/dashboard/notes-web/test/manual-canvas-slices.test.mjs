import assert from 'node:assert/strict';
import test from 'node:test';
import {
  desiredTextCardHeight,
  inlineCanvasSlices,
  normalizedDragBox,
  withInlineCanvasSlice,
} from '../src/regions/manualCanvasSlices.ts';

test('manual image slices preserve planning notes in material metadata', () => {
  const slice = {
    kind: 'region',
    ref: 'region-board-slot',
    representationId: 'shot-clean',
    box: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
    label: '临时槽',
    note: '说明这里要强调临时槽',
    scene: '临时槽已有生成器',
    specialDisplay: '半透明遮罩，仅高亮槽位',
  };
  const material = withInlineCanvasSlice({ material_ref: 'screen:clean' }, slice);
  assert.deepEqual(inlineCanvasSlices(material), [slice]);
});

test('drag boxes normalize, reject accidental clicks, and clamp to the image', () => {
  assert.deepEqual(normalizedDragBox({ x: 0.8, y: 0.9 }, { x: 0.2, y: 0.3 }), {
    x: 0.2, y: 0.3, width: 0.6000000000000001, height: 0.6000000000000001,
  });
  assert.equal(normalizedDragBox({ x: 0.4, y: 0.4 }, { x: 0.405, y: 0.405 }), null);
  assert.deepEqual(normalizedDragBox({ x: -1, y: 0.2 }, { x: 2, y: 0.8 }), {
    x: 0, y: 0.2, width: 1, height: 0.6000000000000001,
  });
});

test('text editing height follows content without producing unusable or unbounded cards', () => {
  assert.equal(desiredTextCardHeight(80), 220);
  assert.equal(desiredTextCardHeight(400), 456);
  assert.equal(desiredTextCardHeight(2_000), 960);
});
