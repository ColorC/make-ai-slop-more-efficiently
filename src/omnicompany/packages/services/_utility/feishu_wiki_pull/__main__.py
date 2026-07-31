# [OMNI] origin=omnicompany domain=utility/feishu_wiki_pull ts=2026-07-22T14:09:05Z type=script status=active
# [OMNI] summary="feishu_wiki_pull 的 python -m 入口"
# [OMNI] why="提供不安装额外 console script 也能执行的稳定入口"
# [OMNI] tags=feishu,wiki,cli,entrypoint

from .cli import main

raise SystemExit(main())
