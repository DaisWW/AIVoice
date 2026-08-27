# Voice Lab 项目规则

Scope: Voice Lab repository root。

这是公司内网使用的多模型声音克隆工具。稳定规则放在本文件，领域细节按任务读取
.codex/ 文档；模型仓库、虚拟环境、运行数据和归档默认不读。

## 默认行为

- 默认用中文交流，除非用户要求其他语言。
- 移动或删除数据前确认精确目标，保留无关工作区改动；优先复用已有服务、持久化、队列、
  引擎和测试模式，不为单次需求增加框架或抽象。

## 项目概览

- web/app/ 是 FastAPI 应用：路由/API、权限、SQLite 持久化、任务队列和引擎适配。
- web/static/ 是浏览器端资源；web/tests/ 用临时 SQLite 和 fake 引擎验证后端与 JS。
- voice_core/ 负责发音解析；code/ 提供音频处理、GPT-SoVITS 和可选模型脚本。
- 所有 GPU 推理必须经过单个可恢复队列；生产仅允许一个 Uvicorn worker，不能用多
  进程或多副本绕过显存与队列约束。
- web/data/、input/、output/、docker-data/ 是运行数据或兼容来源，不属于
  常规源码改动范围；tools/ 是第三方模型依赖，不在普通任务中修改。

## 上下文加载

- 先读本文件，再按任务读取根/模块 README 及最多两份相关 .codex 文档；跨领域才追加。
- Web、Docker、发音/模型任务分别读取对应 README；CR、文档和语义流程分别读取
  code-review.md、doc-writing.md、workflows.md。
- .venv-gpt-sovits/、tools/、archive/、缓存、web/data/ 和 Docker 运行数据默认不读不改。

## 工作流路由

- Bug/崩溃/生成失败/部署症状走 Triage Issue；调用链审查走 Logic Bug Review；模糊方案走 Grill Me。
- FastAPI、SQLite、队列、GPU、上传下载、前端或 Docker 的 CR/精简加载 code-review；
  写文档加载 doc-writing；普通语义流程加载 workflows。
- 用户未明确要求时不主动提交 Git；提交时使用固定中文格式
  “【模块】动词开头的简单说明”，不使用 feat:、fix: 等英文前缀。

## 默认编码闭环

- 明确范围和可验证结果，只改相关文件，保留他人改动。
- 运行最窄检查，查看完整 diff，复核权限、持久化、队列、资源释放和部署影响。

## Python 与 Web 规则

- 使用现有 Black/Ruff 风格和类型标注；import 时不写生产数据、不加载 GPU。
- FastAPI 路由只处理 HTTP、鉴权和输入结构；业务决策放现有服务/领域对象，SQLite
  操作沿用 web/app/persistence/ 的仓储与事务模式。
- 外部 HTTP、文件、音频解码和模型调用必须有超时、输入限制、可定位错误和资源释放；
  API Key、密码、音频和供应商正文不得进日志。
- 上传、路径和导出必须限制在 Settings 管理的根目录中；不接受绝对路径或目录穿越。
- 异步路由不得阻塞事件循环；CPU/GPU 工作经已有 JobQueue 执行。

## 队列、持久化与模型边界

- 任务/候选状态、恢复、取消和导出必须可重试、幂等、可审计；失败不能留下运行中状态或孤立文件。
- 路由和浏览器请求不直接推理；新模型走现有 contracts/registry，切换时释放显存。
- SQLite、脚本编辑、导出和引擎加载沿用现有锁；兼容导入幂等且不删除历史音频、数据库或配置。

## 安全与运行数据

- 密码、Session、供应商 Key、声音样本和台本不得提交、输出或写入固定测试值；API 只返回最小必要数据。
- 对象访问沿用项目归属检查；Docker 不打包模型权重、运行库、上传或输出，生产保持单 worker 和数据卷。

## 完成标准

- 受影响检查通过，或明确说明无法运行的命令和残余风险。
- 完整 diff 不包含运行数据、密钥、模型权重或无关格式化；文档路由与实际模块一致。
