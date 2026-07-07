<!-- [OMNI] origin=claude-code domain=frontend_design ts=2026-07-01T00:00:00Z type=doc status=design -->
# frontend_design — 前端设计与制作管线

> 2026-07-01 新开。把前端"设计→制作→审查"从一次次手改救火, 收成一条可复用的管线:
> 让前端服从一份**持久稳定的规范(标尺)**, 并把设计时的**决策思路沉淀成可复用决策**, 应用到更多地方。
> 内化自 frostpane 的方法论(自底向上分层 + archetype 分治 + 确定性/主观分流 + 不打分用证据);
> frostpane 全部内容已迁入 docs/projects/frontend-design/(仓已删, 避免重复): dashboard 视觉真源在 dashboard/, webgame 标尺留外部指针。

## 状态

status=design。方法与两分支拓扑已定并落地为可跑骨架; 确定性门禁与 VLM 相对评审的真实规则按分支接(webgame 先行试点)。

- 已落: 域骨架(team/formats/run/routers)、两条注册管线(`frontend_design.dashboard` / `frontend_design.webgame`)、共用方法脊柱本文。
- 待接: webgame 分支的确定性探针(walker e2e / webworks/packages)、dashboard 分支的 frostpane 三层门禁、VLM 相对评审(复用 aigc-lab image-review)、决策沉淀写入(接 decisions 域)。

## 核心接口

两条平级子管线(共用方法, 不同标尺):

- `omni run frontend_design.dashboard` —— dashboard 类网页(驾驶舱/poof/lofa)。标尺真源 = frostpane DESIGN.md + theme.css(指针, 不复制)。
- `omni run frontend_design.webgame` —— webgame UI。标尺真源 = webworks/packages/tabletop-engine/README.md(跨消费方红线)+ walker docs/specs。

数据契约(formats.py): `frontend_design.review_request`(入: 要审的界面 + archetype + 标尺 + 基准图 + project)→ `.intake` → `.gate_result`(确定性门禁, 证据列表不打分)→ `.vlm_review`(相对评审, 证据列表不打分)→ `.review_record`(改进建议 + 已沉淀决策)。

## 架构决策

### D1 一域两分支, 共用方法脊柱

dashboard 与 webgame 视觉范式相反(玻璃 shadcn、允许滚动/英文 vs 战场全屏悬浮 HUD、禁滚动禁英文禁网页组件), 但"标尺→确定性门禁→VLM 相对评审→改进闭环→决策沉淀"这套**方法**完全共用。故建一个域、两条平级管线, 差异只在标尺内容与门禁参数(靠 review_request.archetype 分流), 不拆两个域、不建两套机器。

### D2 决策沉淀复用 decisions 域, 不另造库

每次设计判断(去啰嗦文案 / 拆杂物箱成右侧选中面板 / 变体≠状态)落一条 decisions 记录, 普适原则落 belief, 用 rests_on/supersedes 接决策树; 两分支靠 `project` 字段分流(dashboard-design / webgame-ui)。绝不在本域另建决策存储。

### D3 单一真源, 不留重复

dashboard 真源(设计语言 + theme.css + 方法论)已从 frostpane 仓整体迁入 `docs/projects/frontend-design/`, frostpane 仓已删除(避免两份真源打架)。webgame 真源(tabletop-engine README / walker specs)留在 webworks 侧, 本域只放指针(见 webgame/标尺指针.md)。改真源去这两处, 域内 DESIGN 只述方法。

### D4 不打分, 用证据

确定性门禁产 failures 证据列表; VLM 只做相对评审(对基准图成对比较 + 列证据), 绝不输出可信度分。这是硬红线(用户规矩 + 研究证实 VLM 不能打绝对分), 编进 formats 的 schema。

## 数据流 / 拓扑

两分支同构四节点线性管线:

```
intake(RULE) → gate(RULE) → vlm_review(LLM) → synthesize(RULE)
```

- intake —— 归一化审查请求, 锁定 archetype 与标尺, 建 run_dir。
- gate —— 确定性门禁: 跑可判定规则(溢出 / 文案预算 / 对比度 / 字号地板 / 平铺密度…), 产 gate_result(证据列表, 不打分)。
- vlm_review —— VLM 相对评审: 对基准图成对比较, 列证据(不打分)。
- synthesize —— 汇总门禁 + 评审 → 改进建议; 把设计判断沉进 decisions 域(project 分支)。

产物落 `data/domains/frontend_design/`(runs/ rulers/ reviews/ reports/, gitignore)。

## 已知局限

- gate/synthesize 已接真(2026-07-04 第四期): dashboard 分支 gate 真跑确定性审计器 ux_audit
  (`packages/services/_diagnosis/ux_audit`), 错位界面翻成 failures(每条带 L1/L2/L3 分诊,
  词汇=`docs/standards/review/发现分诊三级规范.md`); 审不了的输入(外部 URL/截图/webgame 分支/
  路径不存在)如实降级 `gate_status="skipped-未接入该类目标"`, ux_audit 跑挂则 gate 如实 FAIL,
  绝不假 PASS。synthesize 汇总 failures+comparisons→improvements+报告(写 run_dir),
  把 L3 发现落统一决策库(kind=comment/status=open, 幂等键 alias=fd-run-<run>-<序号>,
  `decisions_recorded` 填真实落库 id), 运行留痕调 `provenance_hook.record_tool_run`
  (留痕失败不阻断)。接真件=`_audit.py`/`_synthesize.py`, 单测在 `tests/domains/frontend_design/`。
- **仍为骨架**: vlm_review(诚实透传桩, 复用 aigc-lab image-review 待接);
  webgame 分支的 gate(walker probes 待接, 本次不做, gate 对 webgame 一律降级 skipped)。
- VLM 相对评审依赖多模态模型接口, 复用 aigc-lab image-review, 未在本域重造。
- 标尺↔落地是单向引用(anchor 指针), 不双向同步真源文档。
- **D5 双源已定一**(2026-07-04): dashboard 视觉真源 = 生产
  `dashboard/frontend/src/styles/frostpane.css`(+`shell/tokens.ts`, main.tsx import 的活文件);
  `docs/projects/frontend-design/dashboard/theme.css` 是设计实验室原型(已加派生标头, 供 demo 用,
  非零引用故不删)。裁决见统一决策库 DEC-2026-07-04-095。

## 参考资料

- 项目文档 home: `docs/projects/frontend-design/`(method/ 共用方法 + dashboard/ 分支主体[设计语言.md / theme.css / demo/ …] + webgame/ 指针); 核心索引 = 该目录 PROJECT_INDEX.md。frostpane 仓已迁入并删除。
- webgame 标尺: `webworks/packages/tabletop-engine/README.md`、walker `docs/specs/battle-ui-spec-v2.md`、`docs/architecture/ui-screen-inventory.md`。
- 决策沉淀: `packages/domains/decisions`(omni decisions record/link/graph)。
- 审阅类型: `docs/standards/review`(拟加 dashboard-page / webgame-screen 类型, 走四步扩展协议)。
