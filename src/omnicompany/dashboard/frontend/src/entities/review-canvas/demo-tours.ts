// 统一设计工作室 · 引导演示脚本注册表(计划 §8.3 第 1 条总验收引导演示)。
//
// 形态权威 = docs/standards/review/引导演示材料规范.md:主审材料是一套跑在真实 dashboard UI 之上的
// 引导演示,覆盖层逐步引导用户过完十个使用场景(计划 §8.1),每步可评论。运行时设施 = wiki-core `demo`
// 模块(mountDemoTour + createDemoCommentStore),这里只写 TourScript(演示步骤唯一真源)。
//
// 目标 CSS 选择器全部复用已存在的 data-testid(ReviewCanvas / StructureView / ProjectDetail),不新造。
// action 一律纯 JSON 声明式:click 点真实按钮 / waitFor 等真实节点 / eval:openProject 跨项目导航
// (跨路由 wiki-core 内置 action 集不覆盖,由 StudioDemoMount 注册的 openProject 钩子承担,见其文件头)。
//
// 现存一条 tour:studio-walker(验收项目 walker,画布三轨+版本链 v1→v2+三条待裁决,覆盖场景一~六、八~十)。
// 决策树三跳段已随裸 DAG 视图撤下(DEC-2026-07-04-240),待"决策树=具象化管线"(DEC-233/239)重做后回归。
//
// narration/title 全中文(中文-only gate:禁英文与开发术语,指称直白,不用比喻框架、不用编号代称)。

import type { TourScript } from '@wiki-core/demo-script'

// walker 主 tour:在真实 walker 项目页上过完材料轨迹画布的使用场景。
const STUDIO_WALKER: TourScript = {
  id: 'studio-walker',
  title: '统一设计工作室 · 材料轨迹',
  app: 'omnicompany-dashboard',
  version: 1,
  steps: [
    {
      id: 'walker-open',
      title: '打开行者项目',
      narration:
        '演示已经替你打开了行者项目页。先说操作方式:全程只用这张卡片上的“下一步”推进,' +
        '页面上该点的地方演示会替你点,你不需要自己动手;卡片挡住内容时可以拖走或最小化。' +
        '下面在这个项目页上把一次审阅从头走到尾。',
      target: '[data-testid="project-detail"]',
      action: { type: 'eval', ref: 'openProject', project: 'walker', facet: 'canvas' },
    },
    {
      id: 'walker-isolation',
      title: '场景一 · 只看本项目',
      narration:
        '默认进入的就是材料轨迹画布,里面只有行者的材料。原先那个把所有项目的材料按时间和类型' +
        '汇聚在一起的审阅台还在,它现在就是全局的待办——待处理的事去那里看,处理完从那里消失;' +
        '项目页里不另设待办。',
      target: '[data-testid="review-canvas"]',
      action: { type: 'click', target: '[data-testid="project-tab-canvas"]' },
    },
    {
      id: 'walker-todo',
      title: '场景一 · 项目内的审阅平列',
      narration:
        '“审阅”这一栏是本项目全部材料的平铺列表,带各自的状态,和画布同一份数据、两种看法。' +
        '跨项目的待办在全局审阅台那边,这里只管本项目。看一眼,下一步切回画布继续。',
      target: '[data-testid="project-tab-reviews"]',
      action: { type: 'click', target: '[data-testid="project-tab-reviews"]' },
    },
    {
      id: 'walker-tracks',
      title: '场景二 · 多版本轨迹',
      narration:
        '切回材料轨迹。画布按轨道分行:界面一览、可玩演示、设计评审各成一条轨。' +
        '同一份稿的多个版本沿版本链横向排开,新版本提交后不用人工摆放,自动落到正确位置。',
      target: '[data-testid="review-canvas"]',
      action: { type: 'click', target: '[data-testid="project-tab-canvas"]' },
    },
    {
      id: 'walker-version-node',
      title: '场景二 · 版本链的第二版',
      narration:
        '高亮的这个节点是重建评审稿的第二版:设计评审这条轨上它有两版,两版之间连着“下一版”的线。' +
        '不用自己点它——下一步演示会替你点开。',
      target: '[data-testid="canvas-node-mat_7dba1df55a454346"]',
      action: {
        type: 'waitFor',
        target: '[data-testid="canvas-node-mat_7dba1df55a454346"]',
        timeoutMs: 8000,
      },
    },
    {
      id: 'walker-detail',
      title: '场景三 · 连线挂着适用规范',
      narration:
        '演示替你点开了第二版,右边弹出的就是它的详情栏(现在高亮的区域)。' +
        '往下翻有一节“适用规范”,列的是这个项目已经拍板的裁决——它们通过“编译进执法器”的关系和材料关联,' +
        '从那里可以再跳到规则文档原文和它源自的用户原话。两版之间的连线也可以点,会切到同一节适用规范。',
      target: '[data-testid="canvas-detail"]',
      action: { type: 'click', target: '[data-testid="canvas-node-mat_7dba1df55a454346"]', timeoutMs: 8000 },
    },
    {
      id: 'walker-rules',
      title: '场景三 · 规则可回溯到原话',
      narration:
        '这就是适用规范列表。每条规则底下带着当初用户说这句话的原文引用,' +
        '再往下能跳到决策树看它编译进了哪个执法函数。规则不是凭空来的,一路可以回溯到源头。',
      target: '[data-testid="canvas-rules"]',
    },
    {
      id: 'walker-comment',
      title: '场景四 · 版本旁写审阅意见',
      narration:
        '每一版旁边都能挂你的审阅意见。在这个输入框里写一条意见,提交后就挂在这一版上,' +
        '刷新页面不会丢,历史意见也一条不少。这条意见经确认后会成为对这一版的修改意见。',
      target: '[data-testid="canvas-comment-input"]',
    },
    {
      id: 'walker-comment-submit',
      title: '场景四 · 意见提交后持久',
      narration:
        '写完点“提交意见”,画布会重载,意见就落在这一版的意见列表里。这里演示的是入口;' +
        '真实提交在自动化回归里跑通:写一条、刷新、意见仍在。',
      target: '[data-testid="canvas-comment-submit"]',
    },
    {
      id: 'walker-next-step',
      title: '场景六 · 从这里发起下一步',
      narration:
        '看完一版,可以直接从它发起下一步实现工作。点“发起下一步”,会把这份材料、适用规范、' +
        '未决意见、相关历史裁决打成一个上下文包复制到剪贴板——最小形态是一键复制成提示词,' +
        '增强形态接派发通道直接送到目标对话。',
      target: '[data-testid="canvas-next-step"]',
    },
    {
      id: 'walker-pending',
      title: '场景六 · 待你裁决的事',
      narration:
        '现在高亮的是“待你裁决”一节:项目里等你拍板的裁决会单列在这里,把阻塞项摆到你面前。' +
        '行者现在就有三条战斗屏相关的待裁决,每条的陈述直接列在这里。' +
        '(原先这里还有一个“决策树”页面,画面不合格已删除;“下次怎么做”的决策树会以具象化管线的形态回来。)',
      target: '[data-testid="canvas-pending-rulings"]',
    },
    {
      id: 'walker-scene5-report',
      title: '场景五 · 工作报告归位',
      narration:
        '工作报告类材料靠轨道字段和“承袭”关系挂到对应产物节点旁,不再混进全局队列。' +
        '行者目前没有单独的工作报告材料,所以画布上暂时看不到这类节点——这是如实说明,不造假数据;' +
        '机制就绪:提交时带上轨道填“工作报告”、承袭指向对应产物,它就会出现在那个产物旁的折叠卡里。',
      target: '[data-testid="review-canvas"]',
    },
    {
      id: 'walker-scene8-label',
      title: '场景八 · 强制打标签',
      narration:
        '关键机制:提交材料必须带上项目、轨道、版本,缺一个就被当场拒绝。这是命令行的真实报错,原文照录——\n' +
        '缺项目:ERROR: project is required — 补 `--project <project>`。\n' +
        '缺轨道:ERROR: track is required — 补 `--track <track>`(如 信息审阅稿/交互审阅稿/工作报告)。\n' +
        '缺版本:ERROR: version is required — 补 `--version <int>`(同一份稿的版本号, 从 1 起)。\n' +
        '标签义务在提交方,不在你。补齐后材料才自动落位到画布。',
      target: '[data-testid="review-canvas"]',
    },
    {
      id: 'walker-scene9-verb',
      title: '场景九 · 标准化动词',
      narration:
        '画布连线和决策边可以标上标准化动词:问题拆分、推导、联想、生成、反证、问题延伸。' +
        '这些动词按真实标注的频率和边界来裁剪定稿,统计口径见动词频率报告和标注报告,' +
        '命令行入口是查看动词统计。这一层是渐进积累的,不靠一次拍脑袋定死。',
      target: '[data-testid="review-canvas"]',
    },
    {
      id: 'walker-scene10-ledger',
      title: '场景十 · 少重复确认',
      narration:
        '最终目标是你少重复确认。设计域的工具运行时会自动检索历史裁决、把用了哪几条写进统一账本,' +
        '这份留痕在 events.jsonl 里可查(账本条目带 consumed_decisions)。' +
        '同一条规矩被你重复叮嘱的次数,随着裁决沉淀应当可观测地下降。',
      target: '[data-testid="review-canvas"]',
    },
  ],
}

// 决策树三跳演示段已随裸 DAG 视图一并撤下(2026-07-04 用户裁决 DEC-2026-07-04-240:该外观绝不应当用)。
// 三跳可回溯(原话→规则→执法函数)的数据契约未变;呈现将随"决策树=具象化管线"(DEC-233/239)重做后再回归演示。

export const STUDIO_TOURS: Record<string, TourScript> = {
  [STUDIO_WALKER.id]: STUDIO_WALKER,
}

/** 引导演示材料 id → 每步评论落到这条材料(target.kind='demo_step')。总验收演示材料(omni review submit --kind demo)。 */
export const STUDIO_DEMO_MATERIAL_ID = 'mat_93a4e074a6d544f4'

export function getStudioTour(id: string | null | undefined): TourScript | null {
  if (!id) return null
  return STUDIO_TOURS[id] ?? null
}
