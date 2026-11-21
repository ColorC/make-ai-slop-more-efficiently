<!-- [OMNI] origin=ai-ide domain=root type=doc status=active -->

# Contributing to omnicompany

面向人类贡献者和 AI agent 的入口指南。规则权威以 [docs/standards/](docs/standards/) 为准——本文件只做最低协作约束，不复制第二份规则。

## 开发环境

```bash
git clone <repo-url> omnicompany
cd omnicompany
pip install -e .
omni --help        # 确认装好
```

更多安装方式见 [README.md](README.md)。需要 LLM 的命令在仓根建 `.env`（见 [.env.example](.env.example)）配好 API key；纯本地命令不需要。**不要提交真实密钥。**

## 测试

```bash
pytest -m "not e2e and not slow"
```

`e2e` / `slow` 标记的测试可能需要真实 API key、外部服务或较长运行时间，只在需要时运行，并在 PR 里写清前置条件与结果。

## 约定

- 文件 / 模块带 OmniMark 头注释；格式与各类规范见 [docs/standards/](docs/standards/)。
- 业务代码归 `packages/domains/`（一个个领域，自带电池），框架不进业务。
- 用 Conventional Commits：`feat:` / `fix:` / `docs:` / `test:` / `chore:` / `refactor:`。每个提交只表达一个清晰意图，服务于可审查性。

## Pull Request

PR 应说明：

- 变更目的和范围；
- 关键文件或模块入口；
- 已运行的验证命令和结果；
- 未运行测试的原因；
- 对安全、配置或外部服务的影响。
