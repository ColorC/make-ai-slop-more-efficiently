"""canvas — 现代 Canvas 存储的领域逻辑（背板域 `canvas`，见 config/backplane/domains.yaml）。

这里只放**与宿主无关**的纯逻辑：投影、检索、观测信封。路由与进程托管仍在
`omnicompany.dashboard.controlplane.overlay_notes` / `canvas_domains`，形状与
`boss_sight` 域一致（域的路由在 dashboard，域的逻辑在 packages）。

`canvas_observation` 原先住在 `dashboard/controlplane/` 下，于是 `omni canvas`
想复用同一份投影就只能 import `omnicompany.dashboard.**`，撞上架构棘轮 R2
（cli/** 不得 import dashboard）。它本来就只依赖标准库，搬到这一层后 dashboard
与 CLI 各自向下取，两边看到的是同一份口径。

没有在原路径留 re-export 壳：壳会让 `patch.object(canvas_observation, "_utc_now")`
打在壳的属性上而函数仍读真模块的全局，monkeypatch 静默失效。搬就整个搬，
调用点一处不落地改。
"""
