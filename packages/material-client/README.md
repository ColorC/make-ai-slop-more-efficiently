# @omni/material-client

Material Provider 公共客户端。它只定义并实现图读取、按需载体读取、订阅和带基础版本的写入语义，不知道 BSide、OmniCompany、端口或文件布局。

产品通过 `createHttpMaterialProvider` 的 URL／解码回调，或直接实现 `MaterialProvider` 接口接入。写入必须携带 workspace、actor、目标、基础 revision 和幂等键；只读 Provider 会显式拒绝写入。
