@echo off
REM 启动 OmniCompany progress service（Rust 服务，:8230）。dashboard / overlay-shell / lofa 都连它。
REM PATH 带上 multica + npm(meegle)，让 meego/multica 同步能 shell 到 CLI。
REM 数据真源: omnicompany/data/services/whatnow; 文件名暂保留用于兼容已有数据。
set "PROGRESS_SERVICE_DATA_DIR=E:\WindowsWorkspace\omnicompany\data\services\whatnow"
set "WHATNOW_DATA_DIR=E:\WindowsWorkspace\omnicompany\data\services\whatnow"
set "PATH=%USERPROFILE%\.multica\bin;%APPDATA%\npm;%PATH%"
cd /d "%~dp0"
echo [progress-service] starting on http://127.0.0.1:8230  (data: %PROGRESS_SERVICE_DATA_DIR%)
"%~dp0target\debug\progressd.exe"
