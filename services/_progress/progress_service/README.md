<!-- [OMNI] origin=codex domain=services/_progress ts=2026-07-04T00:00:00Z type=readme status=active agent=codex -->
<!-- [OMNI] summary="OmniCompany progress service: 进度唯一真源服务。旧 whatnow 命名只作为兼容入口和历史数据文件名保留。" -->
<!-- [OMNI] why="进度是语义 OS 的本体器官, 不应以俏皮项目名作为正名; 服务目录、二进制、CLI 与保活任务统一改成 progress-service/progressd/progress。" -->
<!-- [OMNI] tags=progress,organ,semantic-os,progress-service,internalization -->

# progress-service

OmniCompany 的进度器官: 全机器"进行到哪 / 完成度多少 / 哪些任务置顶"的唯一真源服务。

它仍然绑定 `127.0.0.1:8230`, HTTP API 保持兼容, 数据文件暂保留既有
`omnicompany/data/services/whatnow/whatnow.json` 文件名, 避免把命名整理扩大成数据迁移。
旧 `whatnow` 一词只允许出现在历史记录、兼容环境变量、旧任务字段或旧数据文件名里。

## 当前正名

- 代码真源: `E:\WindowsWorkspace\omnicompany\services\_progress\progress_service\`
- 启动守卫: `ensure_progress_service_running.py`
- Windows 计划任务: `OmniProgressDaemon`
- Rust package: `progress-service`
- daemon: `progressd.exe`
- CLI: `progress.cmd` / `cli/progress.py`

## 运行

```cmd
start-progress-service.cmd
```

或直接运行守卫:

```cmd
E:\WindowsWorkspace\omnicompany\venv\Scripts\python.exe ensure_progress_service_running.py
```

数据目录优先读 `PROGRESS_SERVICE_DATA_DIR`; `WHATNOW_DATA_DIR` 仅作旧脚本兼容。

## API

- `GET /api/board`
- `GET /api/plan-tasks[?plan_id=…]` — 计划的执行子任务(完整字段, lifecycle TaskStore 客户端读路径)
- `POST /api/goals`, `POST /api/tasks`, `POST /api/progress`
- `GET /api/pins`, `POST /api/pin`
- `POST /api/sync/meego`, `POST /api/sync/multica`
- `POST /api/maintenance/auto-archive`

接口名称暂不改, 这是消费方零感知的兼容层。

## 执行子任务（TASK-SSOT-UNIFICATION 2026-07-05）

任务唯一真源也在本服务：`omni plan split` 拆出的执行工单以计划级 task 的子 task
(`parent_task_id`, id 形如 `p_<plan>.<序号>`)存在同一份 whatnow.json。执行子任务
不触发计划完成硬闸、不被 auto-archive 归档；Task 模型的执行字段(details/test_strategy/
dependencies/file_scope 等)全部可缺省且空值不序列化，老数据零迁移。

## CLI

```cmd
progress board
progress tasks --channel demogame
progress add "标题" --channel demogame --ref feishu:<token>
progress progress <task_id> "进度文本"
```

服务地址默认 `http://127.0.0.1:8230`; 可用 `PROGRESS_SERVICE_URL` 覆盖。
