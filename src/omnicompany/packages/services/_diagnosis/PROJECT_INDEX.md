---
omni_project: omni-teambuilder-doctor
name: Team 构建与诊断
group: omnicompany
updated: 2026-06-12
roots:
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis
    note: 主目录(doctor 诊断生态, 约两万行)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_core/team_loader
    note: team 构建器(yaml 与 TeamSpec 互转, 很薄, 116 行)
  - path: E:/WindowsWorkspace/omnicompany/data/doctor
    note: 诊断运行审计日志(极简, 仅 audit 轨迹)
entry_points:
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/cli/commands/team.py
    note: omni team validate/show/run/load 四个子命令(221 行薄框架)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis/doctor
    note: doctor 主体(三条诊断管线 + 四子域约 30 个 Worker + 新方法论 agent)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis/doctor/SKILL.md
    note: doctor 操作手册
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis/doctor/DESIGN.md
    note: 诊断架构权威(管线拆分/不打分只语义标签等设计决策)
  - path: E:/WindowsWorkspace/omnicompany/docs/plans/diagnosis
    note: 诊断计划类目(诊断重整/收束)
latest:
  - "2026-05-16 dashboard 侧立项 team 观察台-doctor-repair 闭环, 见 docs/plans/dashboard/[2026-05-16]team观察台-doctor-repair闭环/"
  - "2026-05-07 诊断重整: 新方法论 agent(规范/假设/样例/计划四型)逐个试跑通过, 但尚未收编旧 Worker, 旧管线仍是生产主力, 见 docs/plans/diagnosis/"
quick_actions:
  - label: 验证team配置
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe team validate --from-yaml=<path> (yaml 能否载成合法 TeamSpec)
  - label: 看team拓扑
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe team show --from-yaml=<path> (节点/连线/入口可视化)
  - label: 诊断Format
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe run doctor.material -i format_id="<id>" -i source_root="<repo>"
  - label: 诊断Worker
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe run doctor.router -i router_id="<类名>" -i source_root="<repo>"
  - label: 诊断管线拓扑
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe run doctor.pipeline-topology -i pipeline_py_path="<pipeline.py>"
  - label: 委托Claude子worker
    skill: omni-claude-worker
    where: E:/WindowsWorkspace/omnicompany
    desc: 把诊断调查/修复实现委托给受审计的 claude-code 子 worker
links: []
---
# Team 构建与诊断

## 概况

两摊东西放一起管, 体量差很大, 如实说:

- **teambuilder(薄)**: omni team 四个子命令 + 一个 116 行的 yaml 加载器。
  Team 是协作 Worker 组的编排单位, 目前纯配置形态——yaml 写拓扑, 验证、可视化、
  执行、注册各一个命令, 没有业务逻辑, 真执行还要调用方补 bindings(节点到
  Router 实例的映射)。
- **doctor(厚)**: 约两万行的诊断生态, 给 Format/Worker/Team 做健康诊断。
  三条独立管线(各自一扇出一汇聚), 四子域约 30 个 Worker 是生产主力;
  另有一条新方法论线(规范型/假设型/样例型/计划型四种诊断 agent + 配套工具),
  逐个试跑通过但还没收编旧 Worker, 两线并行中。

## 当前进展

doctor 旧管线生产可用、服役多次; 新方法论 agent 自 2026-05-05 诊断重整起搭骨架,
到 05-07 四型 agent 都跑通过样例, 收编工作未开始。teambuilder 功能完整且预期就这么薄,
后续如需"代码构建 team"再升级。dashboard 那边 2026-05-16 立了 team 观察台与
doctor-repair 闭环的计划(属 dashboard 类目)。权威文档: doctor/DESIGN.md(架构)、
doctor/SKILL.md(操作)、docs/plans/diagnosis/(计划)。

## 主要目录

- _core/team_loader: yaml 与 TeamSpec 互转, 三个函数就是全部
- cli/commands/team.py: validate/show/run/load
- _diagnosis/doctor/workers: 四子域 Worker(material 12 / team 12 / worker 9 / blackboard 6)
- _diagnosis/doctor/agents: 新方法论诊断 agent(四型 + 假设派生 + 反证器)
- _diagnosis/doctor/builders + tools + scanners: 新方法论配套设施
- _diagnosis 其余子包(cleanup_bot/lap_auditor/pipeline_ci/semantic_auditor/tech_debt 等): 各自独立的小诊断服务
- data/doctor/audit: 运行审计轨迹(非产物库)

## 能做什么

1. team: 验证 yaml 配置、看拓扑、带 bindings 执行、注册到中心
2. doctor 三条管线: 诊断 Format 定义、Worker(Router)、管线拓扑, 经 omni run 调
3. 订阅图诊断: blackboard 子域 Worker(较新)
4. 诊断结论不打分, 只给语义标签(阻断/劣化/建议/信息), 配证据
5. 新方法论 agent: 按规范查、提假设验证、对照样例、按计划核对(骨架期, 慎用于生产)

## 常见展开方式

- 接 team 需求: 先读 docs/standards/concepts/team.md(健康标准), 写 yaml 后 validate + show
- 接诊断需求: 先读 doctor/SKILL.md, 用 omni run doctor.* 跑对应管线
- 诊断结果怎么读: Finding 看语义标签和证据, 别找分数(设计上就没有)
- 改 doctor: 先读 doctor/DESIGN.md 分清旧 Worker 线和新 agent 线, 别混
- 计划上下文: docs/plans/diagnosis/ 两个诊断重整目录

## 自动补漏候选(机器生成,并入正文或删除即可)
<!-- projidx-candidates:begin -->
- [ ] (2026-07-04) 在主要目录/entry_points 或 latest 中补充 _diagnosis/ux_audit，说明其是前端三维 UX 审计 omni pipeline，并可列 run.py/team.py 为入口。 — 证据: 近 30 天 git log: 2026-06-30 "ux-audit 管线: 前端三维 UX 审计(交互/信息/跳转)做成 omni pipeline"；仓内已有路径 E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis/ux_audit/，包含 team.py、run.py、nodes.py、formats.py、enumerators.py。当前 PROJECT_INDEX.md 未列出 ux_audit。
- [ ] (2026-07-04) 将 lap_auditor 从活跃小诊断服务列表中移除或标为已废止/归档，避免读者把 _diagnosis/lap_auditor 当作现行权威入口。 — 证据: 当前 PROJECT_INDEX.md 将 _diagnosis/lap_auditor 归入"各自独立的小诊断服务"；近 30 天 git log: 2026-07-03 "LAP九维检查器显式废止(活引用清零,归档件保留,废止决策已登记)"。路径 E:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/_diagnosis/lap_auditor/README.md 也标注 status=archived，并说明历史代码在 _archive/。
- [x] (2026-07-22) `project-audit` / `project-discovery` / `audit-completeness` 已退出活跃注册并归档。多次真实运行出现超时、无产物或识别 0 个计划项；项目盘点改由 Project Atlas，计划核验改由 `omni plan audit`、测试与独立审阅证据承担。
<!-- projidx-candidates:end -->
