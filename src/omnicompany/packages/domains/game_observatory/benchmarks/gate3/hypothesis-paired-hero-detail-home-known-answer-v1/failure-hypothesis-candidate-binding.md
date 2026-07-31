# 失败假设：候选 provenance 与目标框可被错绑

- 失败样本：`hypothesis-run-01`
- 直接证据：信赖度提交框位于 y=125..240，却引用 y=42..156 的 candidate；头饰提交框位于 y=380..520，却引用 y=701..865 的 candidate。
- 责任层：`propose_probe` 确定性输入校验。
- 修改假设：对每个 `candidate_id` 要求其源像素框与提交目标框的交集至少覆盖较小框面积的 50%，可以阻止跨区域 provenance 错绑，同时允许 locator 框与人工收紧框存在合理尺度差。
- 否证条件：原失败 fixture 重跑时仍写入跨区域 candidate 绑定；或既有正确 candidate 绑定被误拒；或新鲜留出样本出现候选框合理包含关系却无法提交。
- 权限：继续 shadow；本改动不提供设备动作与事实发布权限。