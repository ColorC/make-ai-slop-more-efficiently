import assert from 'node:assert/strict';
import test from 'node:test';
import { annotationStageId, parseAnnotationDocument } from '../src/regions/annotationAnchors.ts';

const fixture = `
<figure data-stage-id="stage-a">
  <span style="position:absolute;left:-25.9%;top:10%;width:151.9%;height:20%" data-target-path="Root/btn#occurrence=1" data-anchor-id="stage-a::producer-hash">1</span>
  <figcaption><section data-anchor-refs="stage-a::producer-hash" data-mechanism-id="local"><b>本地机制。</b> 第一个说明。</section></figcaption>
</figure>
<section data-mechanism-id="cross" data-anchor-refs="stage-a::producer-hash other::hash"><b>跨阶段。</b> 第二个说明。</section>`;

test('consumes producer ids exactly and keeps overflowing normalized geometry', () => {
  const document = parseAnnotationDocument(fixture);
  assert.equal(document.stages.length, 1);
  const anchor = document.stages[0].anchors[0];
  assert.equal(anchor.id, 'stage-a::producer-hash');
  assert.equal(anchor.targetPath, 'Root/btn#occurrence=1');
  assert.equal(anchor.x, -0.259);
  assert.equal(anchor.y, 0.1);
  assert.ok(Math.abs(anchor.width - 1.519) < 1e-12);
  assert.equal(anchor.height, 0.2);
  assert.deepEqual(anchor.mechanisms.map((item) => item.id), ['local', 'cross']);
  assert.match(anchor.mechanisms[0].text, /本地机制/);
});

test('legacy documents degrade to no stages without throwing', () => {
  assert.deepEqual(parseAnnotationDocument('<figure><img src="legacy.png"></figure>'), { stages: [], mechanisms: [] });
});

test('stage identity uses producer metadata instead of recalculating a hash', () => {
  assert.equal(annotationStageId({ id: 'shot-010-game-open' }), '010-game-open');
  assert.equal(annotationStageId({ title: '020-getlike-open' }), '020-getlike-open');
});
