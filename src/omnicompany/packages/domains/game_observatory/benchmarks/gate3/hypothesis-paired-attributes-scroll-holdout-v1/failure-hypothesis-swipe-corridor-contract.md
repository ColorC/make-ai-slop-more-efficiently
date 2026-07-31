# Gate 3 失败假设：swipe 目标框缺少明确的手势走廊合同

## 观察

`hypothesis-run-04` 的来源链已完整。`target_bounds` 下边界与 swipe 起点同为 `y=1640`，起点实际落在半开矩形之外；框只覆盖列表右半部，`visible_cue` 仍描述了左侧属性名。

## 可证伪假设

将 swipe 的 `target_bounds` 明确定义为安全手势走廊，并在运行时强制起止点均位于走廊内、矩形不越出画面，再要求 visible cue 仅描述走廊内像素，同一冻结输入将输出一个完全位于属性面板内且包含整条手势的框。

## 重跑判据

- `target_bounds` 完全位于源画面内。
- swipe 起点和终点均位于 `target_bounds` 半开矩形内。
- 走廊不与角色背景或返回控件重叠。
- `visible_cue` 中的框内内容确实位于走廊；框外线索明确标为邻接锚点。