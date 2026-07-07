---
name: demogame-config-repair
version: 1.0.0
description: "demogame 配表/配置的修复与修改 SKILL（给「maintainer的配置修复机」用）。基于 demogame-auto-config-cli 的 business_runner：按 issue 定位业务→dry-run 预览最小输入与改动→核对→LIVE 写 XLSM/CSV/Lua + 起 P4 changelist（二次确认 + audit.log）→提交由人确认。触发于：配表修复、配置修改、某业务出表错、跨版本补配、复现历史配表 issue。不适用纯collab platform文档操作（走 lark-cli）。"
tags:
  - 格式/skill
  - 操作对象/xlsm
  - 操作对象/python-cli
  - 使用者/AI开发者
metadata:
  authority: "cli/demogame-auto-config-cli/SKILL.md（总入口权威）+ docs/"
  entry: "cli/demogame-auto-config-cli/business_runner.py"
---

# demogame 配置修复机 · 操作 SKILL

> 本 skill = demogame 配表/配置**修复与修改**的可执行流程，给 multica agent「maintainer的配置修复机」与本地使用。
> 范式与术语权威在 `cli/demogame-auto-config-cli/SKILL.md`（分布式业务结构、CLI 命令本能、MI/GT/LIVE 等）。本文件只收口"修一张配置/复现一次修复"的动作序列。

## 一、核心工具与铁律

- 引擎：`cli/demogame-auto-config-cli/business_runner.py`（业务编排：读 `business_workflows/<biz>.yaml` → resolver 派生上下文 → `scripts/<Table>/<biz>.py` 产最小输入 → 写 XLSM）。
- 用法：
  - 预览（安全，无写盘/无 P4）：`python business_runner.py <biz> --version <v> --dry-run`
  - LIVE（写 XLSM/CSV/Lua + 起 P4 changelist）：`python business_runner.py <biz> --version <v>`
- **铁律**：
  1. 任何修改先 `--dry-run` 看清最小输入与将写入的行/字段，核对后才 LIVE。
  2. LIVE 写盘动作默认先 `p4 edit`；改动**到 P4 changelist 为止**。
  3. **绝不自行 `p4 submit`**——提交由人二次确认（LIVE 还需敲 `submit:<biz>:<version>` 校验 token + 落 `audit.log`）。
  4. xlsm 是真源，csv 不可信（跨版本 ID 顺延查 xlsm 全局空闲号）。

## 二、修一张配置 / 改一个业务（标准序列）

1. **读单**：从 issue/meego/multica 描述里抓出"改哪个业务、哪个版本、改什么"。
2. **定位业务**：在 `business_workflows/` 找对应 `<biz>.yaml`（见下"可用业务"）。拿不准就 `demogame-auto-config inspect ...` 只读看表结构/字段/备注。
3. **dry-run 预览**：`python business_runner.py <biz> --version <v> --dry-run`，读它打印的 minimum_inputs 与计划写入；对照 issue 期望逐条核。
4. **核对差异**：用 `demogame-auto-config audit partition <Table>` 比 `<Table>.csv` 与 `_early/_official/_gray` 分区，确认改动面与 scope 一致、无误伤。
5. **LIVE**（确认后）：`python business_runner.py <biz> --version <v>` → 写 XLSM、导 csv/lua、`p4 edit` 起 changelist；按提示敲二次确认 token，落 audit.log。
6. **回报**：把改了哪些表/行、changelist 号、dry-run 与 live 的关键 diff 写回 issue（multica 评论 / meego 单），**不 submit**。

## 三、复现一次历史 issue 修复

- 历史 run 在 `runs/<biz>/<ts>/`（dry-run / live 都留痕）。复现 = 找到对应 `<biz>`+`<version>`，重跑 `--dry-run` 比对历史产物，确认管线能复现真实改动（与 benchmark 的 V1→V2 同思路）。
- 若只为验证而非真改：永远停在 `--dry-run`，产物到 changelist 之前，绝不 submit。

## 四、可用业务（business_workflows/*.yaml）

up_hero_gacha · void_hero_gacha · god_devil_gacha · rerun_gacha · hero_pipe · unit_gallery ·
season_book · season_battlepass · regular_battlepass · activity_battlepass · ep7_phase_battlepass ·
season_fund · season_impulse_gift · up_impulse_gift · void_impulse_gift · hero_impulse_gift ·
season_reward_clear · season_reward_restore · season_switch_fix · gve_season_open · gym_challenge ·
decoration_lottery · dual_capture · stage_milestone · title · gem_goods · avatar_config

（新业务接入走三件套 `business_workflows/<biz>.yaml` + `resolvers/<biz>.py` + `scripts/<Table>/<biz>.py`，绝不在 business_runner.py 加新分支；详见权威 SKILL。）

## 五、不适用 / 边界

- 纯collab platform文档/表格读写 → lark-cli。
- Unity 内真客户端验证 → demogame-unity / unity-cli。
- 战斗数值模拟（非配表）→ demogame-battle-lua-simulator。
- 本 skill 只管"把一张配置按需求改对、改到 changelist、留痕、等人 submit"。
