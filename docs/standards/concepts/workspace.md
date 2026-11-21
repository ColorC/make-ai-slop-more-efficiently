# Workspace · Package 边界与 bus 读写范围

> 2026-04-23 立档 · 由用户 (L1) 明示 · 属基础设施规范
> 权威: 与 `distributed-docs.md` / `pipeline.md` 并列

---

## 一 · 什么是 Workspace

**Workspace = bus 读写范围** — 每个 package (或 team / worker) 对自己的子域声明
**写入紧、读取宽、bash_cwd 紧**, ServiceBus 构造时绑定此声明, 运行时强制.

### 1.1 核心字段 (`runtime/buses/workspace.py::Workspace`)

| 字段 | 语义 | 严格度 |
|---|---|---|
| `name` | workspace 标识 (通常等于 package 名) | — |
| `write_prefixes` | DiskBus.write 允许的绝对路径前缀集合 | **紧** |
| `read_prefixes` | 读允许前缀 (默认 `READ_ANY` 无限制) | 宽 (为 agent 探针留空间) |
| `bash_cwd_prefixes` | BashBus cwd 允许前缀 | **紧** |

### 1.2 典型声明 (package 级)

`.omni/` 目录存放 workspace 元数据。声明方式以 §3.1「声明位置」为准 (推荐 `<pkg>/.omni/workspace.py` 独立文件导出 `workspace` 变量; 也支持 `workspace.yaml` 声明式配置)。下面先用 yaml 形式展示字段含义:

```yaml
# <pkg>/.omni/workspace.yaml
name: team_builder

write_prefixes:
  - src/omnicompany/packages/services/team_builder/
  - data/services/team_builder/

read_prefixes: READ_ANY    # 或 list[str] 限定前缀

bash_cwd_prefixes:
  - ""                      # "" = 项目根
```

Python 代码通过 loader 读取:

```python
from omnicompany.runtime.buses import load_workspace, DiskBus

ws = load_workspace("src/omnicompany/packages/services/team_builder/.omni/workspace.yaml")
disk = DiskBus(workspace=ws)
```

或使用便捷 builder (不走 yaml · 适用于测试 / 动态生成):

```python
from omnicompany.runtime.buses import for_package, Workspace, READ_ANY

ws = for_package("packages/services/team_builder")
```

---

## 二 · 为什么需要 Workspace

### 2.1 防架构漂移污染

> 用户 2026-04-23 原话:
> 每个 package 都要对自己的子域进行 arch 固定, 防止架构漂移污染.

每个 package 是 L3.5 的分布式投影 (见"控制结构.md"). 没有 workspace 约束时, 任何 agent
可能顺手往不属于自己的 package 写文件 — 长期导致 package 架构漂移, 多 package 互污染.

### 2.2 agent-first 探针的安全网

agent-first 哲学 (见 `agent_first.md`) 鼓励 agent 大胆探索 + 建立运行档案.
workspace 作为"写入紧读取宽"的安全网, 让 agent 敢试错不敢出界.

### 2.3 ServiceBus 统一出口设施的合规边界

ServiceBus 是 agent 访问 Disk/Web/Bash/Human 的唯一出口 (见"ServiceBus 定位").
workspace 把 package 边界写进 bus 构造参数, 越界即 BusRejection.

---

## 三 · 使用约定

### 3.1 声明位置 (三选一)

| 位置 | 适用 | 优劣 |
|---|---|---|
| `<pkg>/.omni/workspace.py` | 推荐 · 独立文件导出 `workspace` 变量 | 显式 / 可 import / 易测 |
| `<pkg>/.omni/manifest.yaml` 内字段 | 声明式 package 只读配置 | 与 manifest 同源 |
| `<pkg>/__init__.py` 内 `WORKSPACE = ...` | 临时方案 | 不推荐长期, 混入代码 |

### 3.2 bus 构造

```python
from omnicompany.runtime.buses import DiskBus, BashBus
from .omni.workspace import workspace

disk = DiskBus(workspace=workspace)
bash = BashBus(workspace=workspace)
# 写 package 外 → BusRejection
```

### 3.3 写入扩展

临时扩写范围 (例: 生成另一个新 package 时 team_builder 需要写到 `packages/services/<new>/`):

```python
from omnicompany.runtime.buses import Workspace, READ_ANY

ws = Workspace(
    name="team_builder_generating_X",
    write_prefixes=(
        str(team_builder_root),
        str(new_pkg_root),  # 目标 package 路径
    ),
    read_prefixes=READ_ANY,
    bash_cwd_prefixes=(str(project_root),),
)
```

扩展必须**显式**构造新 Workspace, 不能默认继承扩展. 这强制 agent 思考"我要写哪里".

---

## 四 · 与其他规范的关系

- **ServiceBus 定位** (`runtime/buses/base.py`): workspace 是 ServiceBus 的子参数, 不单独是 bus
- **分布式文档规范** (`distributed-docs.md`): workspace 落地位置 `<pkg>/.omni/workspace.py` 属该规范的 .omni/ 子目录
- **合规 plan** (`docs/plans/[2026-04-23]GUARDIAN-COMPLIANCE-HARDENING/`): Guardian 后续加规则扫描散落写入 (强制新代码走 workspace-bound bus)
- **AgentNodeLoop 纯 Router 化铁律**: agent worker 必须挂 bus, bus 必须有 workspace 约束 — 两铁律联动

---

## 五 · 不做清单

- **不把 workspace 做成全局 state** — 每 bus 实例单独传入, 不用 thread-local / context var (除非极特殊场景)
- **不用 workspace 管 LLM 调用 scope** — WebBus 走 URL 白名单, 和 workspace 正交
- **不用 workspace 做权限模型** — ServiceBus 的 workspace 是"出口约束", 不是"身份/权限", Human Bus 负责权限审批
