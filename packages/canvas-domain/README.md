# Canvas Domain

画布域契约：omnidashboard 感知并操作外部内容域（workshop 等）的统一接口。

## 定位（2026-08-16 架构裁决）

- **omnidashboard 为主体宿主**；workshop 注册为 dashboard 的一个"域"。
- dashboard 与 workshop **零互相依赖**：去掉任一方，另一方功能不受影响（core 包保留）。
  双方只共同依赖 `@omni/material-core`（canvas 契约）与本包。
- 感知**单向**：dashboard → workshop。dashboard 可经 adapter 操作域内容，
  受注册时声明的 `access`（readonly/readwrite）约束。
- 变更语义（单调 revision / base_revision CAS / 幂等 mutation id / 实体级冲突）
  由 `@omni/material-core/note` 统一定义，**与传输形态无关**。

## 两种接入形态（同一接口、两种传输）

| 形态 | 注册字段 | 适用部署 |
|---|---|---|
| `disk`（本包 `./disk` 参考实现） | `root` 路径 | 两者同机 |
| `protocol`（宿主实现 REST/SSE） | `endpoint` + `token_env` | workshop 云端 / 跨机 |

部署矩阵：都本地 → disk 或 protocol；workshop 云端 + dashboard 本地 → protocol；都云端 → protocol。

## 能力协商

`DomainDescriptor.capabilities` 声明域开放的面（canvas / material / wiki / review）。
宿主先经 `descriptor()` 协商再调用；未开放的能力抛 `DomainCapabilityError`。
无 `subscribe` 时宿主轮询 `headCanvas`（`poll_seconds` 配置）。

## 寻址

跨域引用：`omni://domain/<domain_id>/<kind>/<ref>`（kind = canvas|material|wiki|text）。

## 存储布局（disk 形态，与 workshop store / overlay-note-store 兼容）

```
<root>/canvases/<document_id>.json   画布文档（StoredDocument 同形）
<root>/texts/<text_id>.md            材料正文（分居链）
```

## 使用

```ts
import { DomainRegistry, parseDomainConfig } from "@omni/canvas-domain";
import { DiskDomainAdapter } from "@omni/canvas-domain/disk";

const registry = new DomainRegistry();
for (const raw of loadDomainsYaml()) {
  const config = parseDomainConfig(raw);
  if (config.transport === "disk") {
    registry.register(new DiskDomainAdapter(config));
  } // protocol 由宿主提供 adapter 实现
}
```

## 测试

```
node --experimental-strip-types --test test/*.test.mjs
```
