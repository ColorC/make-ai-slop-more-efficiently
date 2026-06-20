# ADR-0001: 将 H-034 降级为局部检查规则

## Status

Accepted

## Context

H-034 曾提出：“没有显式 `condition=FAIL` edge 的 team 可能缺错误处理。”

V19/V20 的批量扫描支持它的早期价值，因为多个 team 在字面扫描下确实没有显式 FAIL 边。

但 V21 和 V26 发现，OmniCompany 中存在多种合法错误处理模式。显式 FAIL edge 只是其中一种。尤其是 csv_to_md 通过节点内部 `Verdict(FAIL)+RETRY` 处理错误，构成 H-034 全局形式的反例。

## Decision

不再把 H-034 作为全局健康规则使用。

H-034 降级为局部检查规则：它只能检查“显式 FAIL edge 模式是否存在”，不能据此判定 team 缺少所有错误处理。

升级后的工作假设为 H-038：team 的合法错误处理可以通过多种模式体现，诊断必须覆盖这些模式。

## Consequences

- 已依赖 H-034 的 finding 需要 recheck 或标记 tainted。
- 后续诊断规则必须区分多种错误处理模式。
- 新实验应从“是否有显式 FAIL edge”转向“是否存在任一可接受错误处理机制”。

## Linked Records

- Claim: H-034
- Claim: H-038
- Conflict: CON-h038-attacks-h034
- Argument: ARG-h034-too-narrow
- Evidence: EV-v21-csv-to-md-fail-retry
- Evidence: EV-v26-five-fail-patterns
