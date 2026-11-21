import assert from "node:assert";
import { validateTour, stepAnchor, executeAction } from "../demo-script.js";

let pass = 0;
const ok = (name, cond) => { assert.ok(cond, name); console.log(`✓ ${name}`); pass++; };

const tour = {
  id: "t1",
  title: "T",
  steps: [
    { id: "s1", title: "A", narration: "走到这" },
    { id: "s2", title: "B", narration: "再走这", action: { type: "waitMs", ms: 1 } },
  ],
};

ok("合法 tour 无警告", validateTour(tour).length === 0);
ok("重复 id 被标", validateTour({ id: "x", steps: [{ id: "a", narration: "n" }, { id: "a", narration: "n" }] }).some((w) => w.includes("重复")));
ok("空 steps 被标", validateTour({ id: "x", steps: [] }).length >= 1);
ok("缺 narration 被标", validateTour({ id: "x", steps: [{ id: "a" }] }).some((w) => w.includes("narration")));

const a = stepAnchor(tour, tour.steps[1], 1);
ok("stepAnchor 形状", a.kind === "demo_step" && a.tour_id === "t1" && a.step_id === "s2" && a.step_index === 1);

let evalCalled = 0;
await executeAction({ type: "eval", ref: "foo" }, { hooks: { foo: () => { evalCalled++; } } });
ok("eval 调 hook", evalCalled === 1);

let cell = null;
await executeAction({ type: "clickCell", q: 2, r: 3 }, { hooks: { clickCell: (q, r) => { cell = [q, r]; } } });
ok("clickCell 调 hook", cell && cell[0] === 2 && cell[1] === 3);

await executeAction({ type: "waitMs", ms: 1 });
ok("waitMs 解析", true);

console.log(`\n${pass} passed, 0 failed`);
