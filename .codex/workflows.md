# Voice Lab 工作流

由根 `AGENTS.md` 按任务路由；产品和部署行为以 README 为准。

## Triage Issue

- 追踪请求、鉴权、持久化、队列、引擎、文件与响应；优先修根因。
- 用临时 SQLite 和 fake 引擎增加最窄回归测试，再做最小修复；避免加载 GPU 或修改运行数据。
- 运行受影响测试；跨层改动再运行 `./run_tests.ps1`。模型/Docker 不可用时明确记录。

## Logic Bug Review

- 按入口、权限、状态/数据流、失败/取消、队列恢复和文件清理组成完整链路。
- Findings-first，只报告有触发条件、证据和影响的问题；修复后复查调用点、测试和完整 diff。

## CR 精简

- 先确定行为基线和允许范围，只处理有证据的重复、死代码、职责混杂、隐藏副作用或耦合。
- 一次精简只做一个主题，不混入重命名、格式化、依赖升级或功能变更。
- 详细检查见 `docs/code-review.md`；改后复查调用点、边界、完整 diff 和最窄测试。

## 模型与迁移

- 修改模型、队列、schema 或 legacy import 前确认单 Worker、显存、状态恢复和历史数据约束。
- 新模型走现有 registry/contract；迁移可重复并保留旧数据。真实模型、GPU、Docker 和声音资产需用户明确授权。

## Grill Me

- 先读代码、README 和模型/队列约束；一次只问一个会改变实现方向的问题，并说明更简单方案和不可逆风险。

## 验证

- 完整检查：`./run_tests.ps1`。
- Python：用 `.venv-gpt-sovits\Scripts\python.exe -m pytest -p no:cacheprovider` 运行最窄 `web\tests` 路径。
- JS：`node --check`，已有测试用 `node --test`；启动/Docker 改动检查 PowerShell/BAT 语法和 README 入口。

## 文档

- 稳定规则放 `AGENTS.md`，领域说明放 `.codex/docs/`，短流程放本文件，历史迁移放 `.codex/archive/`。

## Git 提交流程

仅用户明确要求提交或整理提交时执行：

- 先看 `git status --short`、`git diff --stat` 和目标文件完整 diff；排除无关改动，不使用 `git add -A`、`git commit -a` 或宽泛路径。
- 一条提交只表达一个主题；标题必须是中文 `【模块】动词开头的简单说明`，不使用 `feat:`/`fix:`，建议不超过 30 字。
- 正文写改动与验证，命令显式列出文件；提交后核对 hash、文件数、增删行数和验证结果，不改写公共历史。
