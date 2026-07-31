<!-- [OMNI] origin=claude-code domain=services/_learning agent=ai-ide-e32f0243 ts=2026-06-23T05:54:32Z -->
# 可沉淀 team 骨架草稿: op-fde992

> 候选操作: **探查目录与读取关键文档定位上下文** | 触发: 进入陌生项目时
> 由 conversation-operation-sedimenter 从一段对话自动提议, **草稿**, 待人/team-builder 接力硬化。

## Materials

- `op_fde992.request` (kind=source) — 探查目录与读取关键文档定位上下文 的输入请求
- `op_fde992.s1` (kind=internal) — 第1步产物
- `op_fde992.s2` (kind=internal) — 第2步产物
- `op_fde992.s3` (kind=internal) — 第3步产物
- `op_fde992.result` (kind=sink) — 探查目录与读取关键文档定位上下文 的最终产物

## Workers

- `op_fde992_step1_worker`: Bash 列目录结构  
  FORMAT_IN=`op_fde992.request` → FORMAT_OUT=`op_fde992.s1`
- `op_fde992_step2_worker`: Read 关键入口文档  
  FORMAT_IN=`op_fde992.s1` → FORMAT_OUT=`op_fde992.s2`
- `op_fde992_step3_worker`: 跨 base 搜索比对真实位置  
  FORMAT_IN=`op_fde992.s2` → FORMAT_OUT=`op_fde992.s3`
- `op_fde992_step4_worker`: 确定后续目标目录  
  FORMAT_IN=`op_fde992.s3` → FORMAT_OUT=`op_fde992.result`

## 拓扑

entry = `op_fde992.request`

- op_fde992.request → op_fde992_step1_worker → op_fde992.s1
- op_fde992.s1 → op_fde992_step2_worker → op_fde992.s2
- op_fde992.s2 → op_fde992_step3_worker → op_fde992.s3
- op_fde992.s3 → op_fde992_step4_worker → op_fde992.result

## 本对话其余常见操作(供选别的候选)

- 搜索引用并核对规范口径 (freq≈12): grep 关键词 → 读命中上下文 → 决定改名/迁移
