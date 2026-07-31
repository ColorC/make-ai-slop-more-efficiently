import { registry } from '../entities/registry'
import { workerRegistration } from '../entities/worker'
import { traceRegistration } from '../entities/trace'
import { sessionRegistration } from '../entities/session'
import { noteRegistration } from '../entities/note'
import { planRegistration } from '../entities/plan-folder'
import { settingsRegistration } from '../entities/settings'
import { graphRegistration } from '../entities/graph'
import { teamRegistration, teamBoardRegistration } from '../entities/team'
import { materialRegistration } from '../entities/material'
import { ccSessionRegistration } from '../entities/cc_session'
import { ccCompanionRegistration } from '../entities/cc_companion'
import { multiagentRegistration } from '../entities/multiagent'
import { controllerRegistration } from '../entities/controller'
import { materialRegistryRegistration } from '../entities/material_registry'
import { reviewQueueRegistration } from '../entities/review_queue'
import { reviewMaterialRegistration } from '../entities/review_material'
import { webReviewRegistration } from '../entities/web_review'
import { projectRegistration, projectBoardRegistration } from '../entities/project'
import { questBoardRegistration } from '../entities/quest'
import { authoredRegistration } from '../entities/authored'
import { planAuditRegistration } from '../entities/plan_audit'
// material-graph(裸DAG决策树)组件已删除(2026-07-10 决策本体前端清点:一套图壳=review-canvas,
// 浏览/裁决两姿态;裸 DAG 外观 2026-07-04 已被 DEC-2026-07-04-240 裁死)。
// 决策库图数据 API(/api/v2/material-graph)仍在,由 review-canvas 浏览姿态与 studio_reader 消费。
import { navAuditRegistration } from '../entities/nav-audit'
import { studioReaderRegistration } from '../entities/studio_reader'
import { overlayFileRegistration } from '../entities/overlay_file'
import { fileBridgeRegistration } from '../entities/file_bridge'
import { registerReviewBusinessRenderers } from '../entities/review/businesses'

let registered = false

export function registerAllEntities(): void {
  if (registered) return
  // 材料展示框架(v2 F3): 业务专属类型渲染器与实体同点登记(懒加载, 不改框架代码)。
  registerReviewBusinessRenderers()
  registry.register(projectRegistration)
  registry.register(projectBoardRegistration)
  registry.register(questBoardRegistration)
  registry.register(noteRegistration)
  registry.register(graphRegistration)
  registry.register(planRegistration)
  registry.register(workerRegistration)
  registry.register(teamRegistration)
  registry.register(teamBoardRegistration)
  registry.register(materialRegistration)
  registry.register(controllerRegistration)
  registry.register(materialRegistryRegistration)
  registry.register(reviewQueueRegistration)
  registry.register(reviewMaterialRegistration)
  registry.register(webReviewRegistration)
  registry.register(sessionRegistration)
  registry.register(ccSessionRegistration)
  registry.register(ccCompanionRegistration)
  registry.register(multiagentRegistration)
  registry.register(traceRegistration)
  registry.register(authoredRegistration)
  registry.register(planAuditRegistration)
  registry.register(navAuditRegistration)
  registry.register(studioReaderRegistration)
  registry.register(overlayFileRegistration)
  registry.register(fileBridgeRegistration)
  registry.register(settingsRegistration)
  registered = true
}
