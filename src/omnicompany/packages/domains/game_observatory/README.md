# Game Observatory

外部玩家视角的公开游戏本体、系统设计和玩家声音资料设施。它与
`domains/demogame/ux` 的内部 demogame 设计参考库分开：前者回答公开游戏怎样工作、玩家怎样说，
后者服务内部设计资产复用。

## 已打通的纵向链路

```text
公开/源码来源 + ADB/MuMu 观测
  -> canonical Pydantic objects
  -> SQLite revisions + artifact store + append-only trace
  -> public redaction projection
  -> /api/game-observatory/*
  -> /game-observatory/
  -> Omnicompany reviewstage
```

## 入口

在仓根执行：

```powershell
$env:PYTHONPATH='src'
venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli bootstrap
venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli validate
venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli capture --serial 127.0.0.1:7555
venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli proof
```

看板启动后访问：

- 公开站：`http://127.0.0.1:8210/game-observatory/`
- 健康：`GET /api/game-observatory/health`
- 档案：`GET /api/game-observatory/catalog`
- JSON Schema：`GET /api/game-observatory/schemas/report`
- 写入档案：`POST /api/game-observatory/reports`
- 远程设备截图：`POST /api/game-observatory/capture`

远程写请求需要 `OMNI_GAME_OBSERVATORY_TOKEN` 对应的
`X-Game-Observatory-Token`；本机请求可直接执行。支付、聊天、删除资产和任意 shell
不在归一化动作合同中。

## 数据

运行数据位于 `data/domains/game_observatory/`：

- `observatory.sqlite3`：报告、修订、来源、运行、artifact 元数据与 trace 事件；
- `artifacts/`：截图和 UI hierarchy；
- `fixtures/`：AFK Journey 与 Minecraft 的源真值夹具；
- `exports/catalog.json`：公开档案 JSON 导出；
- `exports/facility-proof.md`：当前设施验证报告。

报告每次语义变化都会追加 revision；相同内容重复导入幂等。公开 API 会隐藏内部源码路径，
canonical store 仍保留精确定位。

## 当前内容

1. AFK Journey：英雄升级的入口、预览、消耗、属性反馈、资源压力与玩家声音。
2. Minecraft Java 1.21.1：从裸手到石镐的工具链、定形配方和客观任务合同。

没有执行完整视觉 benchmark。当前只验证了任务/checker 合同、源夹具和真实 MuMu
Android 15 的截图 + UI hierarchy 采集；MuMu 上 AFK Journey 仍处于安装暂停状态，不能冒充实机升级流程已跑通。