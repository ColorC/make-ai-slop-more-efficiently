# [OMNI] origin=claude-code ts=2026-06-20 type=script
# OMNI-PERSISTENT-SCRIPT owner=productization purpose="PyInstaller 单文件打包入口(release.yml 用); 提交成固定文件, 跨平台稳定(不再内联生成)"
"""PyInstaller 单文件可执行入口 —— 等价于 console_script `omni`。"""

from omnicompany.cli.main import cli

if __name__ == "__main__":
    cli()
