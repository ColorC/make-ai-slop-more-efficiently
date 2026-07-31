# Gate 3 失败假设：视觉硬边界仍不能阻止跨框补字

## 观察

`hypothesis-run-07` 的精确裁片带闭合红框，模型仍从完整原图补写红框外数值。对同一原始裁片运行 RapidOCR，只检测到底部右缘的单字符 `1`，且文字框触及裁片边缘，不能视为完整可转写 token。

## 可证伪假设

将精确裁片 OCR 结果按边缘完整性过滤，生成 `complete_text_tokens`，再把逐字 UI 文本从自由段落中拆成 `visible_text_tokens` 结构字段并做运行时白名单校验，可确定性拒绝跨框补字。裁片没有完整 token 时，最终描述只能保留纹理、线条、颜色、形状与框外锚点。

## 改进与重跑判据

- region inspection manifest 同时保存全部 OCR 结果与未触边的完整 token。
- `visible_text_tokens` 只能精确引用完整 token。
- `visible_cue` 中所有加引号的 UI 字符串和数值声明均接受同一清单校验。
- 当前窄走廊的 `complete_text_tokens` 应为空，最终建议不得出现 665、49、159.9 等数值。