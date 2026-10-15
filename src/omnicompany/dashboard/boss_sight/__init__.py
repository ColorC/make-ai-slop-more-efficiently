# [OMNI] origin=arch-ratchet-surgery ts=2026-08-17 type=shim
"""兼容 shim：boss_sight 已整包迁往 omnicompany.packages.services.boss_sight。

旧路径 import 全部重定向到新位置的同一模块对象（无双实例）。
新代码一律用新路径；本 shim 仅兜历史字符串/动态 import。
"""
import importlib
import importlib.abc
import importlib.util
import sys

_NEW = "omnicompany.packages.services.boss_sight"
_OLD = __name__


class _RedirectLoader(importlib.abc.Loader):
    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        pass


class _RedirectFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == _OLD or fullname.startswith(_OLD + "."):
            real = _NEW + fullname[len(_OLD):]
            module = importlib.import_module(real)
            sys.modules[fullname] = module
            return importlib.util.spec_from_loader(fullname, _RedirectLoader(module))
        return None


if not any(isinstance(f, _RedirectFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _RedirectFinder())

_pkg = importlib.import_module(_NEW)
sys.modules[_OLD] = _pkg
