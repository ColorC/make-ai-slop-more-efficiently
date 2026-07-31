# Gate 3 失败假设：自由框外结构描述仍会跨越检查上下文

## 观察

`hypothesis-run-10` 的框内描述、手势几何、结果、来源和 OCR 清单均合格。最终 `visible_cue` 额外声称左侧相邻可见属性名称列表；region context 的 64px 左侧区域只有空白纹理，属性名称列位于更远的画面左侧。

## 可证伪假设

region 模式的生产 `visible_cue` 默认只描述精确红框内像素。上下文用于人工复核，不能自动进入生产线索；只有显式开启邻接模式并具备结构化 OCR/候选来源时才允许。当前 scene 设为 `exact_target_only` 后，运行时会拒绝 `框外/相邻` 自由叙述，最终建议仅保留已通过的框内纹理与线条。

## 重跑判据

- `region_visible_cue_scope=exact_target_only`。
- `visible_cue` 不含框外或相邻叙述。
- `visible_text_tokens=[]`、`adjacent_text_tokens=[]`。
- 其他已通过字段无回归。