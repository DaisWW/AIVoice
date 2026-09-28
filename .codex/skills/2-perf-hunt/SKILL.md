---
name: 2-perf-hunt
description: Use when the user explicitly requests a full-project performance hunt or scan for Voice Lab, with or without optimization. Do not trigger for a single symptom or ordinary review.
---

# Perf Hunt

仅在用户明确要求全项目性能扫描时使用；普通性能症状走常规工作流。默认先只读检查本仓库自有源码，
第三方模型和真实音频默认跳过；按批次推进并记录覆盖范围。仅当缺失的目标、环境或预算会实质改变结论时询问，
同时继续不依赖答案的检查。结论必须有显存、延迟、队列深度、CPU/Profile、分配或文件大小等证据。

## Workflow

1. 映射音频处理、模型加载/切换、单 GPU 队列、SQLite、上传下载、前端轮询和 Docker 启动。
2. 分批检查并记录范围；优先重复解码、事件循环阻塞、无界读取、显存滞留、队列抖动、轮询和大 DOM/JSON。
3. 报告影响、置信度、证据和验证方法；不以增加 worker、并行 GPU 或削弱权限、校验、恢复和音质换性能。
4. 仅要求扫描时停在报告；请求同时包含优化时，在既有安全边界内做最小改动并复测。
