<!-- [OMNI] origin=claude-code domain=docs/standards ts=2026-04-30T17:00:00Z type=standard -->
<!-- [OMNI] material_id="material:standards.global.verification_invariants_gradient_schema.md" -->

# 标准 · 验证 invariants (W6/W7/PlayerAgent 通用)

> 立档触发: 2026-04-30 L1 看 a 块 demo 实物后明示 — W6 PASS 但实际界面糟糕, vision LLM 漏检. invariants 写得太松导致"PASS"严重失真. 此标准跨 worker 通用 (visual_acceptance_gate / player_agent / 后续 W7 等).
>
> 配套: `feedback_validation_calibration_red_green_gradient.md` (验证管线必带敏感性铁律) / `feedback_connected_is_not_discriminating.md` (连接 ≠ 判别力).

---

## 1. 核心问题

### 1.1 反模式 (现 W6 实例)

```yaml
- invariant: "标题 '选择威胁等级' 在屏幕上显示, 字号大, 颜色明亮可见"
  expected: pass
```

**为什么失败**:
- vision LLM 永远答 "pass" — 任何字号, 任何对比度的标题都"看得见"
- 不区分 demo vs hifi baseline / vs 真 demogame 的差距
- 没有梯度, 好/一般/差 都一个 PASS
- **没有判别力** = 没有验证

实测后果: a 块 demo 跟用户列出的真 demogame (示例游戏) 差距巨大 (没用 backend 真 cellmap / 商店按钮浮动 / 倒计时空跑 / 棋盘看不见棋子 / 等 15 项), W6 4/4 PASS, 严重误导.

### 1.2 正模式

每条 invariant 必须能区分至少:
- **绝对错** (red, fail) — 当前实物某些状态确定不通过
- **基本对** (green, pass) — 当前实物某些状态确定通过
- **梯度** (gradient A/B/C/F) — 区分"好/一般/差/严重差"

测试: invariant 的"反例"是否存在? 如果想不出反例就 fail, 这条 invariant 没价值.

---

## 2. 新 invariant schema

```jsonc
{
  "invariant_id": "S2-battlefield-cellmap",  // 屏 + 项 unique
  "describe": "棋盘必须用 backend /api/battlefield 拉的 demogame cellmap (~45 tiles, 含 banned/block 过渡), 不是 hand-rolled 8x4 死格",

  // 三档敏感性 — 必填
  "red_check": "如果是 8x4 等距矩阵, 没看到 banned 灰格 / block 物体 → fail (说明没接 backend)",
  "green_check": "tiles 数 ≥ 30 + 含 ≥ 1 个 banned 格 + self/oppo 不对称 → pass",
  "gradient": {
    "A": "tiles ≥ 40 + 跟 hifi baseline 元素位置偏差 <10%",
    "B": "tiles ≥ 30 + 含 banned",
    "C": "tiles ≥ 20",
    "F": "8x4 死格 / tiles < 20"
  },

  // 验证方法 — 调度具体怎么验
  "verify_method": "vision_llm_compare_to_baseline",  // 见 §3
  "baseline_path": "designs/06-hifi-final/main_board_prepare.baseline.png",
  "real_demogame_path": "scratch/W7_real_demogame_baseline/main_board_prepare.png",  // 可选

  // 严重度 — 这条若 fail 算什么级别
  "severity_if_fail": "blocker"  // blocker | major | minor | cosmetic
}
```

每条 invariant 必填: `invariant_id` / `describe` / `red_check` / `green_check` / `verify_method` / `severity_if_fail`. `gradient` / `baseline_path` 可选 (但视觉类强烈建议加).

---

## 3. verify_method 4 种

混合用. 越靠后越贵, dom_assertion 优先.

### 3.1 `dom_assertion` (最便宜最可靠)

playwright 直接 query selector + 数 elements / 取 textContent 验.

```js
// invariant: "棋盘 tile 数 ≥ 30"
const tileCount = await page.locator('.battlefield .hex').count();
if (tileCount < 30) return { result: 'fail', evidence: `only ${tileCount} hexes` };
return { result: 'pass', evidence: `${tileCount} hexes` };
```

适合: 数量 / 文案 / 类名存在 / 属性值类 invariants.

### 3.2 `pixel_diff_threshold` (中等)

Python `omnicompany/scripts/visual_diff.py` 用 Pillow + numpy 算 SSIM (Structural Similarity), 阈值给 verdict.

```py
score = compute_ssim(actual_png, baseline_png)
if score >= 0.85: return 'pass'
elif score >= 0.70: return 'partial'
else: return 'fail'
```

适合: "实物跟 baseline 像不像" 类 invariants. **快, 可重现, 数值化**.

### 3.3 `vision_llm_simple` (中贵, 现有)

vision LLM 单图 + 问题 → pass/fail. 跟现 W6 同.

### 3.4 `vision_llm_compare_to_baseline` (最贵)

vision LLM 看 2 张图 (实物 + baseline) + 答相似度 + pass/fail. 用于 pixel_diff 解释不了的"风格"差距.

---

## 4. invariants 撰写守则

### 4.1 必含反例

写 invariant 前自问: "这条 invariant 的 fail 例子是什么?". 想不出就别写.

例:
- ❌ "标题 '选择威胁等级' 显示" — 反例?(任何标题都过)
- ✅ "标题文字精确匹配 spec '选择威胁等级' 字符串" — 反例: 文案 "选择难度", "选择关卡" 都 fail

### 4.2 数值化优先

能数就别问. 模糊形容词 ("字号大", "颜色明亮", "对比度够") 不是 invariant 是描述.

例:
- ❌ "字号大" — 多大算大?
- ✅ "标题 font-size ≥ 18px (CSS computed)" — dom_assertion

### 4.3 锚到 baseline

视觉类 invariants 必须有 baseline (hifi-final / 真 demogame 截图) 作锚. 不锚到具体图就空谈.

例:
- ❌ "棋盘视觉合理"
- ✅ "棋盘截图跟 designs/06-hifi-final/main_board_prepare.baseline.png SSIM ≥ 0.75"

### 4.4 区分 cosmetic / functional

`severity_if_fail` 必填. cosmetic 类 fail 不阻 verdict, blocker 类 fail 直接 fail整屏.

档:
- **blocker**: 功能不可用 (按钮点不响应 / iframe 加载失败 / 棋盘空白)
- **major**: 跟真目标差距大 (棋盘没 cellmap / 商店按钮位置错)
- **minor**: 视觉细节欠 (文案小错 / icon 缺)
- **cosmetic**: 装饰类 (颜色偏 / 边距偏)

### 4.5 路径可达 ≠ 判别力

connected ≠ discriminating. invariant 跑通不代表它在 pass/fail 之间真有区分.

写完每条 invariant, 真去做"红绿试": 故意喂错的实物 (改动让该 invariant 应 fail), 看真 fail 不. 不 fail 就废.

---

## 5. 总览验收

整屏 verdict 派生规则:
- 任一 invariant `severity_if_fail=blocker` 且 `result=fail` → 屏 verdict = `fail`
- ≥ 1 项 major fail → `partial`
- 仅 minor/cosmetic fail → `pass_with_caveats`
- 全 pass → `pass`

总览 verdict (跨 4-6 屏):
- 任一屏 `fail` → 总 `fail`
- ≥ 2 屏 `partial` → 总 `partial`
- 否则 → 总 `pass`

---

## 6. 应用到既有 worker

### 6.1 visual_acceptance_gate (W6)
- 现有 invariants 重写按 §2 schema
- 加 verify_method 调度 (dom / pixel / vision)
- 详见 plans/[2026-04-30]VERIFICATION-INVARIANTS-UPGRADE/road.md phase β

### 6.2 player_agent (PlayerAgent)
- prompt 加 "对照 baseline 严验" 一节
- 加 compare_to_baseline 工具调用
- issue.severity 用 §4.4 4 档

### 6.3 后续 W7 (战斗联调验收)
- 战斗 iframe 视觉验, invariants 含: hp 真变化 / 单位真在动 / events 时间轴 ≥ N 条
- 都按本标准 schema 写

---

## 7. 一句话总览

invariants 必须有判别力 (red/green/gradient 三档敏感) + verify_method 多档 (dom/pixel/vision) + 锚到 baseline + severity 分级. 不满足这些就不是 invariants 是地板. 当前 W6 全是地板.
