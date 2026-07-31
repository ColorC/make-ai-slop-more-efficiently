# UX 设计稿（ux-mockup）格式定义

> gddecon UI 设计生命周期的中间格式。给人看是"设计稿"，对 AI 是 HTML 载体——
> 一组**静态画面**，不做逻辑，只明确**显示信息 + 理论跳转 + 经典样式**。
> 「建立UI设计 / 评估UI / 调整UI」都以这个格式为产物/输入。

## 一、它是什么

一份 UX 设计稿 = 一个自包含文件夹，代表某个屏（如战斗屏）的一套设计：

```
<game>-<scope>-<variant>/          # variant = current(现状) | improved(改进)
  index.html        # 导航图：所有画面缩略 + 跳转关系（点缩略图开画面）
  manifest.json     # 结构化元数据（画面清单 + 跳转 + 批注 + 显示信息）
  ux.css            # 共享皮肤（经典游戏 UI 样式，画面共用）
  screens/
    <screen-id>.html  # 每个静态画面
```

- **静态**：每个画面是一张定格，HTML+CSS 即可，**不写 JS 逻辑**。
- **多画面**：一个屏的不同状态各一张（如 空闲 / 选中我方 / 选中敌方 / 拖游标快进）。
- **跳转是声明的，不是实现的**：画面间的"点这里→去那张"记在 manifest.transitions，index.html 把它画成图；画面本身不需要真能点过去。

## 二、画面 HTML 约定

1. **经典样式**：画面要像一个真实游戏 UI（深色战棋皮肤、棋盘、悬浮 HUD、血条、时间带…），不是线框图。皮肤走共享 `ux.css`。
2. **显示信息齐全**：把这个状态下屏上该有的信息都摆出来（manifest 里也列一份 displayed_info 清单对应）。
3. **顶部画面条**：每张画面顶部一条 `.ux-screenbar`，写画面名 + 用途 + variant，方便审阅时定位。
4. **批注覆盖层**：错误/改正用浮标钉在对应元素上（见下）。

## 三、批注约定（错误 / 改正）

批注是钉在画面元素上的彩色浮标，HTML 里这样标：

```html
<div class="ux-anno ux-anno-error" data-ref="ui.info.focus.01"
     style="left:..;top:..">①未选中却常驻上下文条 · 违反 ui.info.focus.01</div>
```

- `ux-anno-error`（红）：current 稿用，标"这里错了"，`data-ref` 指向被违反的 **UI标准规则 id** 或 **差距条目 id**。
- `ux-anno-fix`（绿）：improved 稿用，标"这里改了"，`data-ref` 指向对应的差距 id（**和盘点一一对应**）。
- 每条批注一句话说清错/改了什么，并在 manifest.annotations 里结构化重复一份。

## 四、manifest.json 结构

```json
{
  "game_name": "行者无乡",
  "scope": "战斗屏",
  "variant": "current",
  "screens": [
    {
      "id": "paused-idle",
      "name": "暂停·空闲态",
      "purpose": "暂停但未选中任何单位时的默认画面",
      "file": "screens/paused-idle.html",
      "displayed_info": ["双方单位+血条+意图", "顶部时间带", "命令点", "暂停态标识"],
      "annotations": [
        {"target": "底部上下文条", "kind": "error",
         "ref": "ui.info.focus.01", "note": "未选中却常驻渲染，违反空闲态仅三要素"}
      ]
    }
  ],
  "transitions": [
    {"from": "paused-idle", "trigger": "点击我方单位赤炎雀", "to": "select-ally",
     "note": "底部上下文条浮现该单位资源+行动卡列"}
  ]
}
```

- `variant`：current（现状稿，批注全是 error）/ improved（改进稿，批注全是 fix）。
- `transitions`：理论跳转图——from 画面、trigger（玩家做什么）、to 画面、note。这就是"明确理论上的跳转逻辑"。
- 一份 current 稿 + 一份 improved 稿，画面 id 对齐，便于左右对照。

## 五、两个产物（对应用户两步）

1. **current 稿**：把当前游戏战斗屏的真实状态还原成画面，批注全部标 error，ref 指向被违反的标准/差距。= "拆分当前错误设计稿，标出错误"。
2. **improved 稿**：针对所有 error 改一版，画面 id 与 current 对齐，批注全部标 fix，ref 指向对应差距 id。= "改进版设计稿，标出改正，和盘点对应"。

## 六、落盘与审阅

- 产物在 `data/knowledge/ux_mockups/<game>-<scope>-<variant>/`。
- 推审阅台用 `omni review submit --kind demo`（可在审阅台内嵌 iframe 直接看画面 + 圈选批注）。
