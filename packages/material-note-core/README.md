# Material Note Core

札记画布的共享数据契约与纯函数。它定义卡片、关系、容器、字号、连线、服务端单调版本、实体级 patch、解析和合并；不包含存储路径、HTTP 地址、主题、React Flow 或正式域判断。

写入端必须显式携带 workspace、actor、base revision 和幂等 mutation ID。Provider 决定保存位置与权限，core 只决定文档和 patch 的语义。
