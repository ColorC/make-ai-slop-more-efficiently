<!-- [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/_test_fixtures ts=2026-05-06T03:30:00Z type=fixture status=active agent=ai-ide -->
<!-- [OMNI] summary="红样本 fixture: 一份故意没'必须/应/不得'强制词的软介绍, HypothesisDeriverAgent 跑应派生 0-1 条假设 (无强制语义无可派生)" -->
<!-- [OMNI] why="self_audit B-2 HypothesisDeriverAgent 红绿基线" -->
<!-- [OMNI] tags=test-fixture,red-sample,derivation-source,no-imperative -->
<!-- [OMNI] material_id="material:diagnosis.doctor.test_fixtures.red_sources.random_readme.md" -->

# 我的工具箱

> 这是我喜欢用的一些工具的非正式介绍.

## 锤子

锤子是一个手工工具. 它有一个金属头跟一个木头柄. 用来敲钉子或者打碎东西. 我家有三把不同尺寸的锤子, 大锤适合打大钉子, 小锤适合精细活. 周末我经常用它修家具.

## 螺丝刀

螺丝刀分一字跟十字两种. 一字螺丝刀的头是一个直片, 用来拧一字槽螺丝. 十字螺丝刀的头是十字形. 我个人比较喜欢电动螺丝刀, 拧得快不累手.

## 卷尺

卷尺用来量距离. 它是一个金属薄片卷起来装在塑料壳里. 拉出来可以量长度, 一般标 cm 跟 inch 两种刻度. 量完按一下按钮就缩回去.

## 我的爱好

除了用工具, 我还喜欢看书, 烤面包, 还有跟孩子一起拼乐高. 拼乐高很解压.

<!-- 故意特征:
- 全文 0 个"必须 / 应 / 应当 / 不得 / 一律 / 永远 / 始终" 强制词
- 全部是描述性陈述, 没"X 应满足 Y" 形态的健康假设候选
- HypothesisDeriverAgent 跑应识别"无强制语义", 派生 0-1 条 (LLM 自律)
- 比拿 worker.md (我之前跑出 5 条) 应明显少
-->
