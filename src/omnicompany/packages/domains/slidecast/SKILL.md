---
name: slidecast
description: AI 自动生成"会动的 HTML 演示式讲解/说书 deck"的领域(类别 aigc-video-content)。把一个选题/脚本变成基于 Slidev 的可交互动画演示(信息密度高、可编程、贴合 HTML 强项),需要时再导出带旁白的视频。何时用:要做知识科普/说书讲解的演示或视频内容时。触发词含 slidecast/讲解视频/说书/科普演示/Slidev/PPT视频/演示生成。不是 AI 生视频素材拼接(那条路线已否)。
---

# slidecast

把一个**选题或脚本**自动变成**会动的 HTML 演示式讲解 deck**(知识科普 / 说书),
需要时再导出成带旁白的视频。类别:`aigc-video-content`(演示 PPT 式视频内容生成)。

引擎选 **Slidev**(Markdown 最好让 LLM 稳定生成、动效体系最全、有官方 AI 工具链、社区最活、MIT 可商用)。
选型证据见 题目1 HTML动画演示路线选型报告。
明确**否掉** AI 生无信息量视频素材拼接那套(旧 AI 视频报告已归档)。

## 何时用

- 要把结构化内容/脚本做成**信息密度高、会动**的 HTML 讲解演示。
- 要把这种演示**导出成带旁白的视频**(讲解/说书)。

## 怎么用

```bash
omni run slidecast.run -i topic="<选题>"        # (管线实现中,见 DESIGN.md 下一步)
```

## 管线(IR-first,见 DESIGN.md)

选题 → 大纲 → 逐页 slide IR(JSON,带 guardrails)→ schema 校验(失败有界重试)
→ 渲染成 Slidev Markdown(v-click/magic-move/Mermaid)→ 构建可交互 HTML →〔可选〕导出带旁白视频。

中间隔一层 **IR(结构化 slide JSON)**:可校验、可重试、内容与表现解耦;
日后若动效不够,换渲染后端(reveal.js)时 IR 不变。

## 状态

雏形(架构已定、管线节点待实现)。下一步见 DESIGN.md。
