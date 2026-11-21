# 显式 FAIL edge 必需性被证伪

这个 v0 fixture 演示推理账本如何记录一条假设从“被提出”到“被支持、被反例攻击、被证否、被降级、派生新实验”的过程。

历史别名：

- `H-034` / `H-2026-05-06-034`：显式 FAIL edge 必需性假设。
- `H-038` / `H-2026-05-07-038`：完整错误处理路径假设。

人类入口应优先使用本目录名和上面的语义名称，编号只用于追溯旧报告和原始 YAML。

## 案例背景

旧诊断工作中曾提出过一个假设：

> H-034：没有显式 `condition=FAIL` edge 的 team 可能缺错误处理。

它一开始有价值，因为字面扫描确实发现多个 team 没有显式 FAIL 边。但后续 challenge 发现，OmniCompany 中合法错误处理不止一种：

- 显式 `condition=FAIL` edge；
- `AnchorSpec.routes` 里的 FAIL 路由；
- 节点内部 `Verdict(FAIL)+RETRY`；
- 外部 driver 控制；
- MaterialDispatcher / 黑板式调度。

因此 H-034 作为“全局规则”被证伪，但它仍可降级为“局部检查规则”。

## 这个样例里有什么

- `claims/H-034.yaml`：原假设，当前状态为 `falsified`。
- `claims/H-038.yaml`：升级后的更宽假设。
- `evidence/EV-v19-batch-finding.yaml`：早期支持 H-034 的批量扫描证据。
- `evidence/EV-v21-csv-to-md-fail-retry.yaml`：攻击 H-034 的反例。
- `evidence/EV-v26-five-fail-patterns.yaml`：进一步攻击 H-034、支持 H-038 的证据。
- `arguments/ARG-h034-too-narrow.yaml`：Toulmin 风格论证，解释为什么 H-034 过窄。
- `arguments/map.argdown`：人读论证图草图。
- `conflicts/CON-h038-attacks-h034.yaml`：显式冲突关系。
- `decisions/ADR-0001-downgrade-h034.md`：决策记录。
- `experiments/EXP-recheck-teams-with-h038.yaml`：后续实验设计。

## 这个样例说明什么

v0 不是证明 H-038 一定正确，而是保证：

1. H-034 为什么出现，有来源。
2. H-034 为什么被证伪，有反例。
3. H-034 不是被删除，而是降级并保留适用边界。
4. H-038 从哪里派生，有关系边。
5. 下一步实验该怎么继续，有实验记录。

这就是“研究账本”的基本价值。
