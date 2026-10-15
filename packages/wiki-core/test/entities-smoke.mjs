import assert from "node:assert/strict";
import {
  createEntityLookup,
  entityDocumentHref,
  findFieldEntity,
  findTableEntity,
  qualifiedEntityTokens,
} from "../entities.js";

const activity = {
  id: "table:demogame/Activity", kind: "配置表", label: "活动表", ref: "table:demogame/Activity",
  aliases: ["Activity", "活动表"], target: "tables/Activity.md",
};
const field = {
  id: "value:demogame/Activity/ActivityID", kind: "配置字段", label: "Activity.ActivityID",
  ref: "value:demogame/Activity/ActivityID", aliases: ["Activity.ActivityID"],
  developerNames: ["ActivityID", "Activity.ActivityID"], description: "活动稳定身份。",
  target: "tables/Activity.md#field=ActivityID",
};
const milestone = {
  id: "table:demogame/PlaneBuffMilestone", kind: "配置表", label: "飞机 Buff 档位表",
  ref: "table:demogame/PlaneBuffMilestone", aliases: ["PlaneBuffMilestone", "PlaneBuffPlaneBuffMilestone"],
  target: "business/PlaneBuff.md",
};
const milestoneField = {
  id: "value:demogame/PlaneBuffMilestone/PlaneBuffID", kind: "配置字段",
  label: "PlaneBuffMilestone.PlaneBuffID", ref: "value:demogame/PlaneBuffMilestone/PlaneBuffID",
  aliases: ["PlaneBuffMilestone.PlaneBuffID"], developerNames: ["PlaneBuffID"],
  target: "business/PlaneBuff.md#field=PlaneBuffID",
};
const lookup = createEntityLookup({ entities: [activity, field, milestone, milestoneField] });

assert.equal(findTableEntity(lookup, "Activity"), activity);
assert.equal(findTableEntity(lookup, "//streams/main/Design/export/server/Activity.json#58 (edit)"), activity);
assert.equal(findFieldEntity(lookup, "Activity#109", "ActivityID"), field);
assert.equal(findFieldEntity(lookup, "Activity.json", "ActivityID"), field);
assert.equal(findFieldEntity(lookup, "Activity", "Activity.ActivityID"), field);
assert.equal(findFieldEntity(lookup, "PlaneBuffPlaneBuffMilestone.json", "PlaneBuffID"), milestoneField);
assert.deepEqual(qualifiedEntityTokens(lookup), [
  ["PlaneBuffMilestone.PlaneBuffID", milestoneField],
  ["Activity.ActivityID", field],
]);
assert.equal(
  entityDocumentHref(field, { projectId: "spark-workshop", basePath: "/workshop/materials" }),
  "/workshop/materials?id=view%3Aspark-workshop%2Fdoc%2Ftables%252FActivity#field=ActivityID",
);

console.log("wiki-core entity lookup smoke passed");
