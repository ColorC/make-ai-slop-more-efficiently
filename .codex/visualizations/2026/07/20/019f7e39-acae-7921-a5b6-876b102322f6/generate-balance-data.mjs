import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

const demoRoot = 'D:/P4/main/AIWorkSpace/app/demo/anniv-fest';
const workshopRoot = path.join(demoRoot, 'src/demos/symbol-workshop');
const output = process.argv[2];
const gamesPerStrategy = Number(process.argv[3] || 1000);
if (!output) throw new Error('output path is required');

globalThis.fetch = async (url) => {
  const rel = String(url).replace(/^\.\//, '').replace(/^\//, '');
  const full = path.join(demoRoot, rel);
  return {
    ok: true,
    status: 200,
    json: async () => JSON.parse(fs.readFileSync(full, 'utf8')),
  };
};

const imod = (name) => import(pathToFileURL(path.join(workshopRoot, name)).href);
const { loadSupplyChainCatalog } = await imod('chain-catalog.js');
const { setUnlockedGroups } = await imod('pick.js');
const { UNLOCK_TRACK } = await imod('meta.js');
const { allDefs } = await imod('state.js');
const core = await imod('sim-core.js');

await loadSupplyChainCatalog();
setUnlockedGroups(UNLOCK_TRACK.map((x) => x.group));

const strategyDefs = [
  ['greedy', '贪心'],
  ['farm', '烹饪'],
  ['wood', '伐木'],
  ['ore', '锻造'],
  ['alchemy', '炼药'],
];
const byId = new Map(allDefs().map((d) => [d.id, d]));
const strategyFns = core.makeStrats(byId);
const samples = [];
const pooled = [];

for (const [key, label] of strategyDefs) {
  const runs = [];
  for (let i = 0; i < gamesPerStrategy; i += 1) {
    const game = core.runGame(1000 + i * 7, strategyFns[key]);
    const run = { strategy: key, label, final: game.finalCoins, curve: game.perRound };
    runs.push(run);
    pooled.push(run);
  }
  samples.push({ key, label, runs });
}

const percentile = (values, p) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.round((sorted.length - 1) * p)];
};
const summarize = (values) => ({
  mean: Math.round(values.reduce((a, b) => a + b, 0) / values.length),
  p25: percentile(values, 0.25),
  p50: percentile(values, 0.5),
  p75: percentile(values, 0.75),
  p90: percentile(values, 0.9),
  p95: percentile(values, 0.95),
  max: Math.max(...values),
});

const maxScore = Math.max(...pooled.map((r) => r.final));
const binCount = 30;
const logMin = Math.log(5000);
const logMax = Math.log(Math.max(5001, maxScore));
const edges = [0];
for (let i = 0; i < binCount; i += 1) {
  edges.push(Math.round(Math.exp(logMin + ((logMax - logMin) * i) / (binCount - 1))));
}
edges[edges.length - 1] = maxScore + 1;

function histogram(values) {
  const counts = Array(edges.length - 1).fill(0);
  for (const value of values) {
    let idx = edges.length - 2;
    for (let i = 0; i < edges.length - 1; i += 1) {
      if (value < edges[i + 1]) { idx = i; break; }
    }
    counts[idx] += 1;
  }
  return counts.map((count) => Number((count * 100 / values.length).toFixed(3)));
}

function roundCurve(runs, p) {
  return Array.from({ length: core.RUN_ROUNDS }, (_, round) =>
    percentile(runs.map((r) => r.curve[round]), p));
}

const strategyOut = samples.map(({ key, label, runs }) => {
  const finals = runs.map((r) => r.final);
  return {
    key,
    label,
    ...summarize(finals),
    histogram: histogram(finals),
    medianCurve: roundCurve(runs, 0.5),
  };
});

const sortedPooled = [...pooled].sort((a, b) => a.final - b.final);
const representative = [
  ['P25 代表局', 0.25],
  ['P50 代表局', 0.5],
  ['P90 代表局', 0.9],
].map(([name, p]) => {
  const run = sortedPooled[Math.round((sortedPooled.length - 1) * p)];
  return { name, percentile: p, strategy: run.key, strategyLabel: run.label, final: run.final, curve: run.curve };
});

const pooledFinals = pooled.map((r) => r.final);
const result = {
  generatedAt: new Date().toISOString(),
  gamesPerStrategy,
  totalGames: pooled.length,
  rounds: core.RUN_ROUNDS,
  edges,
  strategies: strategyOut,
  overall: { ...summarize(pooledFinals), histogram: histogram(pooledFinals) },
  representative,
  note: '当前 headless 模型未消费检查点 BonusReward 的额外选建筑次数。',
};

fs.writeFileSync(output, JSON.stringify(result));
console.log(JSON.stringify({ output, totalGames: result.totalGames, overall: result.overall, strategies: strategyOut.map(({ histogram, medianCurve, ...x }) => x), representative }));
