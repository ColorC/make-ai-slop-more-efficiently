# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=package status=active
"""gddecon — 游戏设计拆解 (Game Design Deconstruction).

读一款游戏的设计源 + 当前 build, 用「方面发现法」(透镜 × 展开规则 × 完备性)
产出该游戏的 *方面树*: 设计应被拆成哪些可评估、可决策的维度
(如 UI > 交互引导性 / 交互存在性 / 信息表达 ...), 每个方面带定义 / 发现透镜 / 证据。

这棵树是「决策树建构」的骨架 —— 用来取代散点式修复。
方法本体在 discovery_method.md (一份可复用、可生长的 material)。

真实执行入口: gddecon.pipeline.run_deconstruction(config)。
"""
