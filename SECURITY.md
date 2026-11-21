<!-- [OMNI] origin=ai-ide agent=codex domain=root ts=2026-06-19T00:00:00Z type=doc status=active -->
<!-- [OMNI] summary="omnicompany 仓库安全报告与密钥管理指南" -->
<!-- [OMNI] why="补齐 GitHub 社区健康文件, 给漏洞报告、版本支持和密钥处理提供公开入口" -->
<!-- [OMNI] tags=community-health,security,secrets,disclosure -->

# Security Policy

## Reporting a Vulnerability

请不要在公开 issue 中发布可复现利用细节、真实密钥、个人数据或未修复漏洞的攻击步骤。

优先通过 GitHub 的 **private vulnerability reporting**（仓库 Security → Report a vulnerability）提交。如果该入口未启用，可创建一个标题为 `Security report` 的最小公开 issue，正文只写影响范围摘要、不含利用细节，维护者会跟进。

## Supported Versions

omnicompany 当前仍是 `0.x` alpha 项目。安全修复优先支持：

| Version / Branch | Supported |
|---|---|
| `main` | Yes |
| Latest tagged release | Best effort |
| Archived branches or old snapshots | No |

## Secrets and Configuration

- 仓根 `.env` 已在 [.gitignore](.gitignore) 中忽略，不应提交。
- 使用 [.env.example](.env.example) 记录变量名和占位值。
- 绝不提交真实 API key、token、cookie、私钥、账号密码或生产连接串。
- 如果密钥误提交，立即撤销或轮换该密钥；不要只靠后续提交删除文件。
- LLM 相关命令可能需要 `THE_COMPANY_API_KEY`，具体说明见 [README.md](README.md)。

## Security Expectations for Changes

- 新增外部服务、网络调用、文件写入或凭据读取逻辑时，在 PR 中说明安全影响。
- 测试和示例只能使用假密钥、占位 token 或本地测试配置。
- 不要把漏洞利用样例、真实事故数据或敏感日志放入 `docs/`、`tests/`、`data/` 或 PR 描述。

