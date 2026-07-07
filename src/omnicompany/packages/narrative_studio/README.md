# Narrative Studio · 叙事内容引擎(原"叙事工作室")

> **2026-07-05 网页壳退役(统一设计工作室 v2 四期, DEC-2026-07-05-025/030)**:
> 用户侧浏览/审阅动线 = 驾驶舱阅读视图(材料展示框架·叙事展示区, `entities/studio_reader`
> + `entities/review/businesses/narrative`);本包**只剩内容引擎**(models/storage/api/health/
> projections/wiki_sync…, 74 测试继续有效, `/api/*` 经 dashboard 反代永久保留)。
> `webui/` 源码留档不删(渲染器"画法"提取的出处), 不再构建、不再托管。
> 结构化编辑照旧走引擎 API/导入器(agent 操作), 不经网页。

无 AI 的叙事**创作 + 审阅**设施与网页端。把一部作品的全部载体(立意 / 揭示层 / 世界 / 角色 / 关系 / 数值 / 结构 / 路线 / 场景 / 成文 / 标签)作为**单一结构化真源**持有,所有视图都是它的投影;按无 AI 成熟写作产品标准(Manuskript / bibisco / Twine / Yarn / ink / Ren'Py / Plottr…)设计。

> 权威设计文档:`故事/vilo-wants-to-know/wiki/{05,07,08}.md`。
> 本阶段**不含 AI 生产/转换**——只做创作、审阅、演练。AI 层日后接在 headless 设施之上。

## P0 决策(已定稿)

1. **分支** = 按稳定 id 的显式 `Connection`(非按名字)。
2. **文本层** = 独立 `ProseLine` 按行 id 寻址(结构层只引用,先语义后文风)。
3. **立意** = 轻量 `premise` 块 + 可选 `storyform` 脚手架(不强制 Dramatica 重模型)。
4. **落盘** = 每载体一份 pretty-JSON、文件夹组织、可 git diff(原子写 + `.history/` 修订快照)。
5. **交互** = 轻反应性:条件/效果/变量(Bool/Int/String + 命名空间),无重调度器。

## 架构(刻意 UI 解耦)

```
models.py       格式契约(Pydantic;Project 持全部载体)
storage.py      每载体 JSON 落盘 + 原子写 + 修订快照/还原
expr.py         轻反应性求值(条件/效果/初始 state)
importer.py     vilo 讨论稿(seeds/00-17) → Project(00=真源)
projections.py  时间线/大纲/节点图/关系图/角色场景/变量引用/标签出现/钻取/分布/溯源
health.py       12 类结构性健康检查
playthrough.py  演练求值器(沿图走、应用条件效果、判结局/揭示触发)
queries.py      完成度 / 空字段 / 全文搜索
api.py          FastAPI:把上面暴露给网页端 + 实体 CRUD + 静态托管前端
webui/          Vite + React + reactflow 前端(三栏:导航/视图/检查器)
```

数据:`omnicompany/data/narrative_studio/projects/<id>/`(vilo 已导入)。

## 运行

```bash
# 1. 构建前端(一次)
cd webui && npm install && npm run build && cd ..

# 2. 启动(后端服务 + 托管前端),浏览器开 http://127.0.0.1:8330
python -m omnicompany.packages.narrative_studio          # 等价 serve --port 8330

# 开发模式(前端热更):
#   终端A: python -m omnicompany.packages.narrative_studio   (后端 :8330)
#   终端B: cd webui && npm run dev                            (前端 :5319,自动代理 /api)

# 从讨论稿重生成 vilo 项目:
python -m omnicompany.packages.narrative_studio import-vilo
```

测试:`PYTHONPATH=src venv/Scripts/python.exe -m pytest src/omnicompany/packages/narrative_studio/tests/ -q`

## 嵌入 omnidashboard

后端独立服务于 :8330,前端为自包含 SPA。omnidashboard 可通过链接或 iframe(`http://127.0.0.1:8330`)嵌入;后端是普通 FastAPI 应用,也可挂载进 dashboard 的 ASGI 路由。

## 当前装载内容

vilo(《Vilo想知道》):平行宇宙(Alters)版。立意=用户口述的哲学母题(故事无关);只装已认可方向 + 游戏内容(cards/events),具体情节/结构/大纲/文风未认可故留空,被取代/否决项进归档。缺口由完成度仪表盘与健康检查逐一暴露。
