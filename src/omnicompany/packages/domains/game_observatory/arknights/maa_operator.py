# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T07:12:00Z type=maa-operator status=active
# [OMNI] summary="封装 MaaAssistantArknights 的受限执行、事件与强制刷新取帧通道。"
# [OMNI] why="语义层需要统一执行端和可证明新鲜的独立画面，不能散落裸绑定调用。"
# [OMNI] tags=game-observatory,arknights,maa,operator,capture
"""MAA 操作设施：把 MaaAssistantArknights 封成可复用的执行与取帧通道。

存在理由（ISR-R18）：导航、开始作战、暂停、部署／撤退／开技能这些**重复动作**必须由设施
承担，绝不允许调用方肉眼估坐标后硬点。MAA 已经把这些做得又快又稳（tasks.json 869 个任务、
2147 张模板图、finetune 过的 PaddleOCR、三个战斗小模型），因此按分流规则**落进 MAA**，
不自造。

MAA 已确认覆盖的能力（读 var/vendor/MAA/resource 真源得到，非文档推断）：
    导航      ClickChapter1/2/3、ToChapter1/2/3、EnterEpisodeNew、StageTheme
    开始作战  StartButton1（OCR「开始行动/开始作战/开始推演」）、BattleStartAll 分派
    练习模式  BattleStartExercise
    暂停周期  BattlePause、BattlePauseCancel、BattleDeployWhenPause、BattleSpeedUp
    单步战斗  SingleStep 任务（subtype=stage/start/action）
    感知      OCR（PaddleOCR/PaddleCharOCR）、模板匹配、operators_det/skill_ready_cls/
              deploy_direction_cls 三个 onnx
MAA **不覆盖**：战斗中敌人的位置／类型／血量／异常状态识别（onnx 只有干员检测，无敌人
模型）。该部分是本项目自建增量，不在本模块职责内。

许可注意：MAA 为 AGPL-3.0-only 并附加用户协议。本模块通过其官方 Python 绑定以子进程／
动态库方式调用，不复制其源码。产出物若需对外分发，须先做许可评估（见计划 R-8）。
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

__all__ = [
    "MaaMessage",
    "MaaEvent",
    "MaaPaths",
    "MaaOperator",
    "MaaNotAvailable",
    "MaaTaskFailed",
    "TouchMode",
]


class MaaNotAvailable(RuntimeError):
    """MAA 安装缺失、绑定不可导入，或资源加载失败。"""


class MaaTaskFailed(RuntimeError):
    """任务链以失败终止。"""


class MaaMessage(IntEnum):
    """回调消息类型（对齐 asst.utils.Message）。"""

    InternalError = 0
    InitFailed = 1
    ConnectionInfo = 2
    AllTasksCompleted = 3
    AsyncCallInfo = 4
    Destroyed = 5
    TaskChainError = 10000
    TaskChainStart = 10001
    TaskChainCompleted = 10002
    TaskChainExtraInfo = 10003
    TaskChainStopped = 10004
    SubTaskError = 20000
    SubTaskStart = 20001
    SubTaskCompleted = 20002
    SubTaskExtraInfo = 20003
    SubTaskStopped = 20004


class TouchMode:
    MINITOUCH = "minitouch"
    MAATOUCH = "maatouch"
    ADB = "adb"
    MAA_FW_ADB = "MaaFwAdb"


_TERMINAL_OK = {MaaMessage.AllTasksCompleted}
_TERMINAL_BAD = {
    MaaMessage.InternalError,
    MaaMessage.InitFailed,
    MaaMessage.TaskChainError,
}


def _bounded(
    fn: Callable[[], Any], timeout: float, what: str
) -> Any:
    """给无超时的阻塞 C 调用套上时限。

    MaaCore 的 ``AsstLoadResource`` / ``AsstConnect`` / ``AsstAsyncScreencap`` 都是同步阻塞
    且**不接受超时参数**的：设备卡死或资源损坏时它们可以永不返回，从而挂住整个进程。
    （本项目已被无超时命令挂死过约 8 小时，因此约定所有可能阻塞的调用都必须有上限。）

    局限必须说清楚：超时只能让**调用方**脱身，DLL 内部无法取消，那个工作线程会以守护线程
    身份留在后台直到进程退出。故超时后应视会话不可信，重建而非复用。

    :raises MaaTaskFailed: 超时。
    """
    import threading

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 需要跨线程回传
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True, name=f"maa-{what}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise MaaTaskFailed(
            f"{what} 超过 {timeout}s 未返回；该会话应作废重建。"
        )
    if "error" in box:
        raise MaaTaskFailed(f"{what} 抛错: {box['error']}")
    return box.get("value")



@dataclass(frozen=True, slots=True)
class MaaEvent:
    """一条结构化回调事件。"""

    message: MaaMessage
    details: dict[str, Any]
    monotonic: float

    @property
    def what(self) -> str:
        return str(self.details.get("what", ""))

    @property
    def taskchain(self) -> str:
        return str(self.details.get("taskchain", ""))

    def __str__(self) -> str:
        bits = [self.message.name]
        if self.taskchain:
            bits.append(f"chain={self.taskchain}")
        if self.what:
            bits.append(f"what={self.what}")
        return " ".join(bits)


@dataclass(frozen=True, slots=True)
class MaaPaths:
    """MAA 安装布局。"""

    root: Path
    user_dir: Path

    @classmethod
    def locate(
        cls, root: Path | str | None = None, user_dir: Path | str | None = None
    ) -> "MaaPaths":
        root = Path(
            root
            or Path(__file__).resolve().parents[6] / "var" / "vendor" / "MAA"
        )
        user = Path(user_dir) if user_dir else root.parent / "_maa_user"
        return cls(root=root, user_dir=user)

    @property
    def python_binding(self) -> Path:
        return self.root / "Python"

    @property
    def core_dll(self) -> Path:
        name = {
            "win32": "MaaCore.dll",
            "darwin": "libMaaCore.dylib",
        }.get(sys.platform, "libMaaCore.so")
        return self.root / name

    @property
    def tile_pos_dir(self) -> Path:
        return self.root / "resource" / "Arknights-Tile-Pos"

    @property
    def overlay_dir(self) -> Path:
        """本项目的增量资源目录。

        MAA 支持 ``AsstLoadResource`` 二次调用做覆盖加载（``Asst.load`` 的
        ``incremental_path``），布局须为 ``<dir>/resource/tasks/tasks.json``——这与 MAA 自
        身 ``resource/global/<client>/`` 的组织方式一致。

        我们用它承载 **MAA 原生缺失** 的识别（例如新手引导遮罩：MAA 全部 869 个任务中
        没有任何一个处理引导）。这样增量能力仍然长在 MAA 框架内部，复用它的 OCR 与模板
        匹配引擎，而不必自己写一套点击逻辑，也就不会出现手算坐标。
        """
        return Path(__file__).resolve().parent / "maa_overlay"

    def validate(self) -> None:
        missing = [
            str(p)
            for p in (self.root, self.core_dll, self.python_binding / "asst" / "asst.py")
            if not p.exists()
        ]
        if missing:
            raise MaaNotAvailable("MAA 安装不完整，缺少: " + ", ".join(missing))


@dataclass
class _Sink:
    """线程安全的事件汇集。MAA 回调来自其内部线程。"""

    events: list[MaaEvent] = field(default_factory=list)
    _q: "queue.Queue[MaaEvent]" = field(default_factory=queue.Queue)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    done_ok: threading.Event = field(default_factory=threading.Event)
    done_bad: threading.Event = field(default_factory=threading.Event)

    def push(self, ev: MaaEvent) -> None:
        with self._lock:
            self.events.append(ev)
        self._q.put(ev)
        if ev.message in _TERMINAL_OK:
            self.done_ok.set()
        elif ev.message in _TERMINAL_BAD:
            self.done_bad.set()

    def drain(self) -> list[MaaEvent]:
        with self._lock:
            return list(self.events)

    def stream(self, timeout: float = 0.2) -> Iterator[MaaEvent]:
        while True:
            try:
                yield self._q.get(timeout=timeout)
            except queue.Empty:
                return


class MaaOperator:
    """MAA 的一次会话。负责资源加载、连接、任务执行与事件收集。

    典型用法::

        op = MaaOperator.create()
        op.connect(adb_path, "emulator-5554")
        op.run([("StartUp", {})])

    本类只做「把任务安全地跑起来并拿到结构化回执」。**语义动作（导航到某关、进练习模式、
    暂停下干员）应在此之上另建薄封装**，以免把 MAA 任务名散落到业务代码里。
    """

    _resource_loaded: bool = False

    def __init__(self, asst: Any, paths: MaaPaths, sink: _Sink, callback: Any) -> None:
        self._asst = asst
        self._paths = paths
        self._sink = sink
        self._callback = callback  # 必须保持引用，否则 ctypes 回调被 GC
        self._connected = False

    # ---- 构造 ----

    @classmethod
    def create(
        cls,
        root: Path | str | None = None,
        user_dir: Path | str | None = None,
        incremental: Path | str | None = None,
        *,
        load_overlay: bool = True,
        load_timeout: float = 120.0,
    ) -> "MaaOperator":
        """创建会话。

        :param load_overlay: 是否加载本项目的增量任务（``maa_overlay/``）。默认加载，
            这样 ``Omni@*`` 任务在 ``Custom`` 里随时可用。
        :param load_timeout: 资源加载上限。``AsstLoadResource`` 是无超时阻塞调用。
        """
        paths = MaaPaths.locate(root, user_dir)
        paths.validate()
        paths.user_dir.mkdir(parents=True, exist_ok=True)

        binding = str(paths.python_binding)
        if binding not in sys.path:
            sys.path.insert(0, binding)
        try:
            from asst.asst import Asst  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise MaaNotAvailable(f"无法导入 MAA Python 绑定: {exc}") from exc

        if not cls._resource_loaded:
            if not _bounded(
                lambda: Asst.load(path=paths.root, user_dir=paths.user_dir),
                load_timeout,
                "AsstLoadResource",
            ):
                raise MaaNotAvailable(f"AsstLoadResource 失败: {paths.root}")
            extras: list[Path] = []
            if load_overlay and (paths.overlay_dir / "resource").is_dir():
                extras.append(paths.overlay_dir)
            if incremental:
                extras.append(Path(incremental))
            for extra in extras:
                if not _bounded(
                    lambda e=extra: Asst.load(
                        path=paths.root, incremental_path=e
                    ),
                    load_timeout,
                    f"AsstLoadResource({extra.name})",
                ):
                    raise MaaNotAvailable(f"增量资源加载失败: {extra}")
            cls._resource_loaded = True


        sink = _Sink()

        @Asst.CallBackType
        def _cb(msg: int, details: bytes, _arg: Any) -> None:  # pragma: no cover
            try:
                payload = json.loads(details.decode("utf-8") or "{}")
            except Exception:
                payload = {"_raw": repr(details)}
            try:
                m = MaaMessage(msg)
            except ValueError:
                m = MaaMessage.InternalError
            sink.push(MaaEvent(m, payload, time.monotonic()))

        return cls(Asst(callback=_cb), paths, sink, _cb)

    # ---- 生命周期 ----

    @property
    def paths(self) -> MaaPaths:
        return self._paths

    @property
    def connected(self) -> bool:
        return self._connected

    def version(self) -> str:
        return self._asst.get_version()

    def set_touch_mode(self, mode: str = TouchMode.MAATOUCH) -> bool:
        from asst.utils import InstanceOptionType  # type: ignore

        return bool(
            self._asst.set_instance_option(InstanceOptionType.touch_type, mode)
        )

    def set_deployment_with_pause(self, enabled: bool = True) -> bool:
        """开启「暂停下干员」。这是暂停周期执行模型的关键开关。"""
        from asst.utils import InstanceOptionType  # type: ignore

        return bool(
            self._asst.set_instance_option(
                InstanceOptionType.deployment_with_pause, "1" if enabled else "0"
            )
        )

    def connect(
        self,
        adb_path: Path | str,
        address: str,
        config: str = "General",
        *,
        timeout: float = 90.0,
    ) -> bool:
        """连接设备。

        ``AsstConnect`` 是**无超时的阻塞 C 调用**：模拟器未启动、adb 卡死或 touch 方案协商
        失败时它可以一直不返回，从而挂住整个进程。故套上 :func:`_bounded` 时限。

        :raises MaaTaskFailed: 超过 ``timeout`` 仍未返回（此时会话应作废重建）。
        """
        self._connected = bool(
            _bounded(
                lambda: self._asst.connect(str(adb_path), address, config),
                timeout,
                f"AsstConnect({address},{config})",
            )
        )
        return self._connected


    # ---- 任务执行 ----

    def run(
        self,
        tasks: Sequence[tuple[str, dict[str, Any]]],
        *,
        timeout: float = 300.0,
        poll: float = 0.2,
        on_event: Callable[[MaaEvent], None] | None = None,
    ) -> list[MaaEvent]:
        """追加任务链并阻塞执行到终态，然后把实例复位以便下次调用。

        复位是必须的：``AsstStart`` 在实例仍处于运行态时会直接返回 false，而
        ``AllTasksCompleted`` 回调**早于** MAA 内部线程真正停下。若不等待并 ``stop()``，
        第二次 ``run()`` 会以 ``AsstStart 失败`` 告终。此行为由真机连续调用实测确认。

        :raises MaaTaskFailed: 任务链报错或超时。
        """
        if not self._connected:
            raise MaaTaskFailed("未连接设备，先调用 connect()")

        self._quiesce()  # 确保干净起点

        for name, params in tasks:
            if self._asst.append_task(name, params) <= 0:
                raise MaaTaskFailed(f"append_task 失败: {name}")

        self._sink.done_ok.clear()
        self._sink.done_bad.clear()
        seen = len(self._sink.events)
        if not self._asst.start():
            raise MaaTaskFailed("AsstStart 失败")

        deadline = time.monotonic() + timeout
        try:
            while True:
                if on_event:
                    for ev in self._sink.drain()[seen:]:
                        seen += 1
                        on_event(ev)
                if self._sink.done_bad.is_set():
                    raise MaaTaskFailed(self._failure_summary())
                if self._sink.done_ok.is_set():
                    break
                if not self._asst.running():
                    time.sleep(poll * 3)
                    if not self._sink.done_ok.is_set():
                        break
                if time.monotonic() > deadline:
                    raise MaaTaskFailed(f"任务超时 {timeout}s")
                time.sleep(poll)
        finally:
            self._quiesce()
        return self._sink.drain()

    def _quiesce(self, timeout: float = 10.0, poll: float = 0.05) -> bool:
        """停止并等待实例真正空闲，同时清空任务队列。

        :return: 是否在期限内静默。
        """
        self._asst.stop()
        deadline = time.monotonic() + timeout
        while self._asst.running():
            if time.monotonic() > deadline:
                return False
            time.sleep(poll)
        return True

    def stop(self) -> bool:
        return bool(self._asst.stop())

    def running(self) -> bool:
        return bool(self._asst.running())

    def _failure_summary(self) -> str:
        bad = [
            str(e)
            for e in self._sink.drain()
            if e.message in _TERMINAL_BAD
        ]
        return "任务链失败: " + ("; ".join(bad) if bad else "(无详情)")

    # ---- 取帧 ----
    #
    # 实测结论（2026-08-08，emulator-5554，MaaCore v6.16.5）：
    #   AsstGetImage 返回的是 **PNG 编码图**，不是裸 BGR。官方绑定 docstring 写「如
    #   1280*720*3」会误导——那个参数只是**缓冲区容量**，不是像素布局。
    #   图像尺寸是 MAA 的规范工作空间 **1280x720**，而非设备分辨率（本机 1920x1080）；
    #   这与 tasks.json 中全部 roi 均为 1280x720 坐标一致。
    #   与 adb screencap 同屏交叉比对：mean|diff|=1.60，within8=96.61%（残差来自界面动画
    #   与缩放插值），判定为同一画面。
    #   耗时对比：本通道约 47ms，adb screencap+pull 约 657ms —— 快约 14 倍。

    #: MAA 规范工作空间。其 tasks.json 的 roi/坐标均在此空间内。
    CANONICAL_SIZE = (1280, 720)

    #: 取帧缓冲容量。PNG 最坏情况可能超过原始像素量，给足余量。
    _FRAME_CAPACITY = 1920 * 1080 * 3

    #: ``AsstGetImageBgr`` returns the canonical continuous BGR frame without
    #: PNG encoding. MaaCore's public C header defines ``AsstSize`` as
    #: ``uint64_t`` and its controller cache is the 1280x720 workspace.
    _BGR_FRAME_BYTES = CANONICAL_SIZE[0] * CANONICAL_SIZE[1] * 3

    @staticmethod
    def _trim_png(buf: bytes) -> bytes | None:
        """从固定容量缓冲里截出完整 PNG。

        绑定不按实际写入长度截断，缓冲尾部是填充。按 PNG 的 IEND 块定位真实结尾：
        chunk 结构为 ``[len:4][type:4][data][crc:4]``，IEND 无 data，故结尾 = IEND 下标 + 8。
        """
        if not buf or not buf.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        idx = buf.find(b"IEND")
        if idx < 0:
            return None
        return buf[: idx + 8]

    def capture_png(self, *, fresh: bool = False) -> bytes | None:
        """取 MAA 截图的 PNG 字节。无可用截图时返回 ``None``。

        :param fresh: 是否先强制刷新。默认 ``False`` 沿用缓存帧（省一次截图开销）。

        **务必注意缓存语义**：MAA 只在自身执行任务时刷新截图，空闲时反复取回同一帧
        （实测连续 5 次调用字节数与耗时完全相同）。做「动作前后对比」时必须
        ``fresh=True``，否则会拿同一帧比出「无变化」的假结论。
        """
        if fresh and not self.refresh_frame():
            return None
        raw = self._asst.get_image(self._FRAME_CAPACITY)
        if raw is None:
            return None
        return self._trim_png(bytes(raw))

    def capture_bgr(self, *, fresh: bool = False) -> bytes | None:
        """Return one canonical raw BGR frame without PNG encode/decode.

        The ABI comes from the vendored MaaCore ``AsstCaller.h`` and
        ``AsstCaller.cpp``: ``(handle, void*, uint64_t) -> uint64_t``. Any
        short, empty, or sentinel result fails closed instead of exposing a
        partial image to the battle-control classifier.
        """

        import ctypes

        if fresh and not self.refresh_frame():
            return None
        fn = getattr(self._lib(), "AsstGetImageBgr", None)
        if fn is None:
            return None
        fn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64)
        fn.restype = ctypes.c_uint64
        buffer = (ctypes.c_ubyte * self._BGR_FRAME_BYTES)()
        size = int(fn(self._handle(), buffer, self._BGR_FRAME_BYTES))
        if size != self._BGR_FRAME_BYTES:
            return None
        return bytes(buffer)

    def raw_buffer(self, capacity: int | None = None) -> bytes | None:
        """低层入口：原样返回 ``AsstGetImage`` 的缓冲（含尾部填充）。

        仅用于诊断格式问题。常规取帧请用 :meth:`capture_png`。
        """
        return self._asst.get_image(capacity or self._FRAME_CAPACITY)

    # ---- 官方 Python 绑定未暴露、但 MaaCore.dll 确实导出的能力 ----
    #
    # 绑定只包了 17 个符号，DLL 导出表里还有 AsstAsyncClick / AsstAsyncScreencap /
    # AsstGetImageBgr / AsstBackToHome / AsstConnected / AsstGetUUID / AsstGetTasksList
    # / AsstAttachWindow 等（由 DLL 字符串表核对得到）。其中前两个对本项目关键：
    #   * AsstAsyncScreencap 让我们能**按需取新鲜帧**，而不必等 MAA 自己跑任务；
    #   * AsstAsyncClick 让「感知算出的坐标」也能走 MAA 同一条触控通道，
    #     不必另开 adb 通道造成执行端分裂。
    # 这两个是补齐 IO 闭环的关键，故在此直接以 ctypes 绑定。

    def _lib(self) -> Any:
        lib = getattr(type(self._asst), "_Asst__lib", None)
        if lib is None:
            raise MaaNotAvailable("MaaCore 尚未加载")
        return lib

    def _handle(self) -> Any:
        ptr = getattr(self._asst, "_Asst__ptr", None)
        if ptr is None:
            raise MaaNotAvailable("Asst 实例句柄不可用")
        return ptr

    def refresh_frame(self, *, block: bool = True, timeout: float = 30.0) -> bool:
        """让 MAA 立刻重新截图（``AsstAsyncScreencap``）。

        这解除了「只能拿到旧帧」的限制。``block=True`` 时同步等待完成——该等待在 DLL 内部
        无超时，设备卡死会永不返回，故套上 :func:`_bounded` 时限。
        """
        import ctypes

        fn = getattr(self._lib(), "AsstAsyncScreencap", None)
        if fn is None:
            return False
        fn.argtypes = (ctypes.c_void_p, ctypes.c_bool)
        fn.restype = ctypes.c_int
        return int(
            _bounded(
                lambda: fn(self._handle(), bool(block)),
                timeout,
                "AsstAsyncScreencap",
            )
            or 0
        ) != 0


    def tap(self, x: int, y: int, *, block: bool = True) -> bool:
        """在 MAA 的坐标空间（``CANONICAL_SIZE``，即 1280x720）点击一次。

        **仅供设施内部使用**：坐标必须由感知（OCR／模板／几何投影）计算得出，
        不允许由调用方目测硬填。语义层请使用 :mod:`operations` 中的意图方法。
        """
        import ctypes

        w, h = self.CANONICAL_SIZE
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"坐标 ({x},{y}) 越出 MAA 空间 {w}x{h}")
        fn = getattr(self._lib(), "AsstAsyncClick", None)
        if fn is None:
            raise MaaNotAvailable("MaaCore 未导出 AsstAsyncClick")
        fn.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_bool)
        fn.restype = ctypes.c_int
        return int(fn(self._handle(), int(x), int(y), bool(block))) != 0

    def is_connected_now(self) -> bool:
        """向 MaaCore 查询实时连接状态（``AsstConnected``）。"""
        import ctypes

        fn = getattr(self._lib(), "AsstConnected", None)
        if fn is None:
            return self._connected
        fn.argtypes = (ctypes.c_void_p,)
        fn.restype = ctypes.c_bool
        return bool(fn(self._handle()))

    def back_to_device_home(self) -> bool:
        """调用 ``AsstBackToHome``。

        **命名陷阱**：它回的是**安卓系统桌面**，不是游戏主界面——实测调用后游戏被切到
        后台、画面变成模拟器桌面。想回游戏主界面请用 ``StartUp`` 任务
        （:meth:`operations.ArknightsOperations.go_to_main_screen`）。
        """
        import ctypes

        fn = getattr(self._lib(), "AsstBackToHome", None)
        if fn is None:
            raise MaaNotAvailable("MaaCore 未导出 AsstBackToHome")
        fn.argtypes = (ctypes.c_void_p,)
        fn.restype = ctypes.c_bool
        return bool(fn(self._handle()))


    # ---- 事件 ----

    @property
    def events(self) -> list[MaaEvent]:
        return self._sink.drain()

    def stream_events(self, timeout: float = 0.2) -> Iterator[MaaEvent]:
        return self._sink.stream(timeout)
