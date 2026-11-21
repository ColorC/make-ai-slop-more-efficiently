<!-- [OMNI] origin=claude-code domain=docs/standards ts=2026-04-24T00:00:00Z type=standard status=active -->
<!-- [OMNI] material_id="material:standards.global.workspace_isolation_test_discrimination.md" -->

# 软件工作区两条控制论铁律 · Workspace Isolation + Test Discrimination

> **立档时间**: 2026-04-24 · 由 voxelcraft Walk 2/3 实战踩出 · L1 明示"控制论铁律"级别
>
> **适用范围**: 跨所有 omnicompany 子系统 · 凡涉及**生成产物 + 运行验证**的 pipeline/agent 工作流, 都受本文两条铁律约束. 不是建议, 是默认配置项.

---

## 一 · 问题来源 (为什么需要这两条)

任何"生成 → 验证"型 pipeline (voxelcraft mod 生成 · data 管线 · 代码重构 agent · 配表自动化 · ...) 都会面对两类底层问题, 若不**结构性解决**必然反复踩:

**问题 A · 状态污染 (State Pollution)**: 多次 run 共享同一个 workspace 目录时, 前一次 run 留下的状态 (文件 / 数据库 / 运行时持久化 NBT / 缓存 / lock) 影响下一次 run 的语义. 症状: "本地好使提交挂" · "单跑过了批量跑不过" · "昨天过了今天不过". 手动加 cleanup 只是**补丁**, 补不完.

**问题 B · 伪通过 (False Positive Tests)**: 验证用的测试只查"过程信号" (如 log 字符串出现), 不查"效果真达成". LLM/代码产生的错误实现可能**既产正确信号又产错误效果**, 测试会放行. 症状: "测试绿但线上爆" · "审计发现一直是假通过".

这两类问题**不是靠经验增长消掉**, 是**靠架构规定消掉**. 本文定铁律.

---

## 二 · 铁律 I · 状态隔离 (Workspace Isolation)

### 定义

**每个 agent run / pipeline invocation / 生成单元 的文件系统工作区必须是独立的**. 不允许多个 run 共写同一个目录.

### 实现形式 (三选一, 视基础设施选择)

| 形式 | 怎么隔 | 适用 |
|---|---|---|
| **git worktree** | `git worktree add <path> HEAD` | 目标 repo 是 git · Walk 3+ **首选** |
| **目录 cp -r / rsync** | 每 run 完整拷一份基线 | 非 git repo |
| **ephemeral container** | Docker / LXC / chroot | 需要进程隔离 + 文件隔离 |

### 硬规则

1. **Worker 代码里禁止写死 workspace 路径常量** (例如 `_ETERNAL_WAR_ROOT = "E:/..."`). 必须参数化为 `working_root`, 由 orchestrator (run.py) 注入.
2. **一 run 一 root** · run 开始建, 归档后释放 · run 期间任何 Worker 都只读/写**该 root 下**.
3. **禁止 Worker 之间靠共享目录通信**. Worker 间数据流走 Material (Format), 不走文件系统隐式依赖.
4. **持久化状态 (DB / ops.json / playerdata / cache) 是 workspace 的一部分**, 必须进隔离边界.

### 反例: 主目录未提交 source 反污染 worktree (2026-04-25 实测)

**症状**: redstub_e2e 跑时 mineflayer 报 `Failed to decode packet 'serverbound/minecraft:hello'` 100% 复现, 但单独跑 connect_only / clean build 都 OK. 控制变量长链调查 (build 工具链/source 注入/pre-RCON/dig_at vs connect_only) 都没复现.

**真根因**: 主目录 eternal-war 有 **20 个未提交修改** (开发期 WIP + 04-24 17:07 旧脚本直接写 main 的残留). worktree 用 cp -r 拷贝, 把这些**未提交的脏 source** 也带进 worktree, 与 baseline 偏离. 具体哪一字节触发 mineflayer hello 解码错误 — 没追到, 因为这是状态混合的副作用而非清晰因果.

**修法**: commit 主目录所有未提交合法改动作为 baseline (4 commits), worktree cp 后变成 known-good. 所有控制变量实验在 baseline 上重跑全 OK.

**预防 (硬规则)**: 凡是用 worktree / cp -r / rsync 做隔离的 pipeline, **必须先 git status 主目录确认干净**, 否则隔离假象 (你以为 worktree 独立, 实际带着主目录的脏状态偏离). orchestrator 启动时应自检 baseline 是否干净, 不干净要么报错要么 commit 后再跑.

**已落实**: `voxelcraft/common/workspace_context.py::_assert_baseline_clean` · worktree 创建前 git status 检查, 脏状态直接 raise. opt-out: `voxelcraft_ALLOW_DIRTY_BASELINE=1`.

### 反例 2: mineflayer username 超 16 字符 (2026-04-25 实测 · 4h 坑)

**症状**: voxelcraft redstub_e2e 跑 mineflayer dig_at probe 报 `Failed to decode packet 'serverbound/minecraft:hello'` 100% 复现. 各种环境/build/source 控制都没复现.

**真根因**: redstub_e2e 用 `f"probebot_{int(time.time())}"` 作 username (= `probebot_1777094882` 19-20 字符), MC 1.21 LoginStart packet username 字段 max **16 字符**, server 端 netty decoder 抛 DecoderException, server 通用日志只显示 "Failed to decode packet 'serverbound/minecraft:hello'" — 看似协议层错误, 实际是 username 长度超限.

**判别力盲区**: server 错误信息**没明示** username 长度违规. 调试时容易误判成 protocol 不兼容 / build 工具链漂移 / mod 干扰. 控制变量法 (5 轮: build/source/pre-RCON/action/uname) 才把变量收敛到 username 字符长度.

**修法 (已落)**:
1. `_probe_bot_action` 加 username 长度 guard, 超 16 截断 + warn
2. `redstub_e2e` 改用 `pb_<HHMMSS>` (9 字符)
3. 单测 `bot_username_smoke_test.py` 防退化

**预防 (硬规则)**: 凡是协议有"软上限/硬上限"字段, **client 库不报错时 server 报错**的常见模式, 必须**用边界值压一遍**. probe 库要主动校验, 不能"原样发出去看 server 错回来" — 因 server 的错误信息往往是聚合后的笼统 "decode failed", 失去定位价值.

### 反例 (voxelcraft Walk 2/3 实测)

- Walk 2 · `pioneer_gift` 第 2 次 probe 失败: ProbeBot 同名 + 同 playerdata, attachment persistent=true 保留了第 1 次的"已激活"状态, 第 2 次 probe 以为机制不工作.
  - **错误补丁**: 换 username 每 run. 头疼医头.
  - **铁律做法**: 新 workspace = 新 world = 新 playerdata, 根本没状态可漏.

- Walk 2 · `mechanism/*.java` cleanup · 加代码 _cleanup_prior_mechanism_files + 剥 EternalWarMod register 行 · 逻辑越积越复杂.
  - **错误补丁**: 更多 cleanup 函数.
  - **铁律做法**: worktree 天然是白纸, cleanup 函数**全部删掉**.

### 违反检测

每个新建的 Worker / pipeline orchestrator 必须在 DESIGN.md §架构决策 声明自己的 workspace 隔离形式. TeamChecker / design_validator 检测到硬编码路径常量即报警.

---

## 三 · 铁律 II · 测试判别力 (Test Discrimination · TDD 式)

### 定义

**验证产物的测试, 必须先证明它能区分"对的"和"错的", 才能用它作 gate**. 没证明判别力的测试 = 装饰, 不算 gate.

### TDD 式实现

1. **先写测试 (包含 probe 的 effect 检查, 不是只看信号)**
2. **红验证**: 喂一个"已知不正确" 的 stub / minimal broken impl → 测试必须 FAIL. 若 stub 也过 → 测试无判别力, 不能用.
3. **绿验证**: 喂一个"已知正确" 的 stub / minimal working impl → 测试必须 PASS.
4. **红绿都做过再生成真代码**: 然后把实际 LLM / 开发者产出喂进同一测试. 此时 pass/fail 才有意义.

### 测试结果是色谱不是二分 (2026-04-25 用户明示)

**色谱 ≠ 测试粒度**. 色谱指的是: **凡有测试, 必有从"完全失败"到"最简成功"再到"完整成功"的渐变光谱**. 对 LLM 产物尤其重要 — LLM 失败的语义梯度大, 二分 PASS/FAIL 信息量不足.

典型色谱 (以 voxelcraft mechanism `/placeblock` 为例, 由浅入深排):
1. **编译都过不了** (语法错 / import 错)
2. **编译过但机制没注册** (调用漏挂到 onInitialize)
3. **机制注册了但 log 关键字写错** (tier2 失败)
4. **log 对了但效果没生效** (tier3 失败 · 我们抓到的)
5. **效果生效但参数错位** (坐标偏 1 / 异常被吞 / try-catch 错位 / 部分参数错)
6. **基本对但有副作用** (额外动了别的状态 / 资源没释放 / 性能极差)
7. **完全正确**

**判别力测试不能只造端点**. 端点对照只证明 "极端绿/极端红能区分", 中间档才能反推:
- LLM 产出的真实质量梯度 (落在哪一档)
- prompt 还需要哪些约束
- probe 是否在某些中间档有"假通过"或"假失败"

**进阶建议**: 红绿对照只是色谱的最弱形态. 完整色谱测试要造 ≥ 4 个梯度档位, 验证 probe 输出的判决与实际质量档位**单调对应**. 否则 probe 只是二分类器, 信息量低.

voxelcraft 当前色谱状态 (2026-04-25 已补): **5 档真 E2E 验证 probe 单调对应**:
- 脚本 `scripts/voxelcraft_tier3_color_spectrum_e2e.py` 跑 5 档 stub: not_registered (档2) / wrong_log_keyword (档3) / log_no_setblock (档4) / wrong_coords (档5) / correct (档7)
- 真编 5 jar + 真 MC server + RCON probe · **5/5 单调对应** · 无假通过/假失败
- **关键观测 · 档 5 (wrong_coords)**: log 对 + 真 setBlock 但偏 1 格. tier2 PASS (log 命中), tier3 真查 `execute if block X Y Z diamond_ore` → Test failed (因为 X,Y,Z 是 air, 方块在 X+1,Y,Z). 这是"做对一半"型 bug 的典型代表, 工程实践最常见
- 档 1 (compile fail) 由 Compiler 守门, 不在本测试 · 档 6 (副作用) 列入已知盲区, 需 tier4/observability 才能抓

进阶: 这个色谱跑一次需 ~3min, 不入 daily smoke. 但**任何 probe 重构后必跑**作为回归. 已加单测 `tests/domains/voxelcraft/mechanism/color_spectrum_smoke_test.py` 守 stub 模板的字串完整性 (静态), 真色谱 E2E 由 `scripts/` 下脚本人工触发.

### 测试强度分级 (probe 三 tier, 越深越强)

| tier | 查什么 | 能抓什么 | 抓不到什么 |
|---|---|---|---|
| 1 · 协议固有 | 系统协议保证的固定字段 (如 MC /summon 响应 "Summoned new" · HTTP 200) | "code 完全没注册/没跑起来" | 代码跑起来但行为错 |
| 2 · 期望子串 | LLM / 开发者约定的 log 字符串 | "该 log 的分支没走" | "走了但返回码错" |
| 3 · **效果状态检查** | **对目标系统状态的直接查询** (e.g. `/execute if block X Y Z diamond_ore` 确认方块真没被破坏; HTTP 登录后 session 存在; DB 新行真插入了) | **业务逻辑是否真生效** | 极端并发 / 分布式幻觉 (需 tier4 · observability) |

**铁律要求**: probe 必须**至少**含 tier 1. 对返回码敏感 / 状态修改型机制, **必须**含 tier 3.

### 反例 (voxelcraft Walk 2/3 实测)

- Walk 2 · `diamond_gate` (advancement_gated) probe 通过 `[diamond_gate]` log 字串匹配 approved=True. 但 LLM 若产"log 了但 return true" 的 bug, log 照样 fire, probe 仍 approved=True — **假通过**.
  - **错误补丁**: 把 log 字串设得更具体. 无效.
  - **铁律做法**: probe 加 tier 3 · dig 后 RCON **bare** `execute if block <pos> minecraft:diamond_ore` 返回 `Test passed` 才算 approved (方块真没被破坏). **注**: `run say FOO` 不会 echo 到 RCON, 必须用 bare execute 拿 Test passed/failed (2026-04-24 经验校准).

- Walk 2 · `gaia_blessing` 第 1 次运行 build 通过, bot dig 后 server NPE 崩 · probe 没查 runtime exception.
  - **铁律做法**: probe 加 tier 3 · 跑完扫 log 是否含 "java.lang.*Exception" / "Caused by:" 模式.

### 违反检测

Designer/Engineer 产的 probe spec 必须声明 tier1 + (tier2 可选) + tier3 (若适用). 没有 tier 3 的返回码敏感机制, LoadChecker 发 Verdict.FAIL 前置拦, 不让进 Compiler 阶段.

### voxelcraft 参考实装 (2026-04-24 ~ 2026-04-25 完成)

- 核心模块 `packages/domains/voxelcraft/common/probe_effects.py` · `run_effect_checks(specs, *, rcon_send) -> (bool, list[str])`
- schema: `effect_checks: list[{type: "rcon_query", cmd, expected_contains, expected_not_contains, note}]`
- 已注入 4 处 probe: `_probe_log_scan` / `_probe_rcon_command` / `_probe_bot_action` / `_probe_entity_summon`
- Designer prompt 写有 5 类典型范例 (advancement_gated / attack_cancel / counter / item_give / entity_spawn)
- **RCON 响应格式经验校准**: `/execute if block ...` **bare** (不带 `run say`) · 条件成立返 `Test passed`, 不成立返 `Test failed` · `run say FOO` 不 echo 到 RCON (见 `scripts/voxelcraft_tier3_rcon_format_calibration.py`)
- **判别力自证 · mock 级**: 8 个集成测试 `tests/domains/voxelcraft/common/probe_tier3_integration_test.py` 断言 tier2 被 log 欺骗时 tier3 抓到 bypass
- **判别力自证 · 真 E2E 级** (2026-04-25): `scripts/voxelcraft_tier3_placeblock_e2e.py` 造 `/placeblock` 命令两变体, green 真 setBlockState, red 只 log 不 set · 跑真 gradle build (23s green + 11s red) + 真 MC 1.21.1 server + RCON probe · **green tier2+tier3 双 PASS · red tier2 被 log 骗过但 tier3 真 MC 查方块 `execute if block` 返 `Test failed` 抓到 bypass · matched=False** · 非 mock 的铁律 II 落地
- 16 unit + 8 integration + 1 真 E2E = **24 test + 1 E2E 三层色谱齐全 (mock → build → real server)**
- **完整闭环 · LLM 自填 tier3 + 全链 E2E** (2026-04-25): `scripts/voxelcraft_tier3_designer_prompt_eval.py` 5 题 matrix 8/8 过 · `scripts/voxelcraft_tier3_fullpipeline_e2e.py` 真 LLM Designer+Engineer+Compiler+LoadChecker 全链 91s 跑通, LLM 自己产 `effect_checks: [{execute if block ..., Test passed, note: "Verify diamond_ore actually placed at target coords"}]`, approved=True, tier3 真 MC 返 Test passed

其他子系统落两铁律时应复用同一 `run_effect_checks` 模式 (接注入的 rcon_send 或等价 query hook).

---

## 四 · 两条铁律的相互关系

铁律 I (隔离) 处理 "**同一套测试跑多次, 结果应一致**" — state 不泄漏就可重复.
铁律 II (判别力) 处理 "**测试通过, 代码就真的对**" — 测试不放假正.

两条缺一不可:
- 只 I 不 II: 每次 run 干净, 但测试太弱, 错代码也过.
- 只 II 不 I: 测试严格, 但前一次 run 的状态串扰, 导致后一次测试偶发 FAIL/PASS (flaky).
- I + II: run 可重复 + 结果可信. 这是**可以发布可以验证**的起点.

---

## 五 · 与其他 standards 的关系

- **workspace.md** (旧): 描述 `.omni/workspace.yaml` 的 path 约束, 偏权限 · 不涉及 run-per-run 隔离.
- **agent_first.md**: 信息库先行 · 是本文铁律 II tier3 的前置条件 (Designer 要知道如何写 effect check).
- **information_sufficiency.md**: 节点输入充分性 · 本文铁律 II 是"输出验证"的对称面.
- **llm_first.md**: LLM 能力铁律 · 不覆盖本文两条.

本文是**新增**的横切规范, 不替换任何现有规范.

---

## 六 · 实施优先级

按**已暴露问题数量** 排:
1. voxelcraft (3 路径已跑) · Walk 3 立即补两条 · 做为试点 ← 2026-04-24 起
2. demogame 配表管线 · 状态污染问题同样存在 (xlsm 共享) · Walk 后置
3. config_service agent · 尚未成熟, 设计期即内嵌两条

每个子系统在 DESIGN.md §架构决策 必须有一节明示如何落两条铁律.

---

## 七 · 参考材料

- voxelcraft Walk 2 mechanism audit · [docs/plans/[2026-04-24]voxelcraft-MECHANISM-WALK-2/handoff_2026-04-24.md](../plans/[2026-04-24]voxelcraft-MECHANISM-WALK-2/handoff_2026-04-24.md) · 首次把问题结构化暴露
- TDD 本源 · Kent Beck, "Test-Driven Development By Example" (2002) · 红-绿-重构三步
- git-worktree(1) 官方 man · `https://git-scm.com/docs/git-worktree`
- MSFT xUnit.net 文档 · test isolation 章节
