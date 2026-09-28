---
name: 1-bug-hunt
description: Use when the user explicitly requests a full-project bug hunt or bug scan for Voice Lab, with or without fixes. Do not trigger for an ordinary bug report or a scoped review.
---

# Bug Hunt

仅在用户明确要求全项目 Bug 扫描时使用；普通故障和局部审查走常规工作流。默认先只读审查本仓库自有源码，
第三方模型、虚拟环境、归档和运行数据默认跳过；按批次推进并记录覆盖范围。仅当缺失的目标或预算会实质改变
扫描结果时询问，同时继续不依赖答案的检查。

## Workflow

1. 映射 FastAPI、鉴权、持久化、队列/状态、引擎、文件/音频、前端和 Docker 入口。
2. 分批检查并记录范围；优先越权、路径穿越、密钥、卡死/重复任务、SQLite/文件分歧、线程和 GPU 生命周期。
3. 按 P0/P1/P2/P3 报告触发条件、影响、文件/行号、证据和复现/推理。
4. 仅要求扫描时停在报告；请求同时包含修复时，在既有安全边界内做最小修复并重跑最窄测试；跨层改动再运行 `run_tests.ps1`。
