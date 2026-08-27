---
name: 1-bug-hunt
description: Use only when the user explicitly requests a full-project autonomous bug hunt for Voice Lab. Do not trigger for an ordinary bug report or a scoped review.
---

# Bug Hunt

仅在用户明确要求全项目 Bug 扫描时使用；普通故障和局部审查走常规工作流。默认先只读，
确认范围、预算和是否允许修复；第三方模型、虚拟环境、归档和运行数据默认跳过。

## Workflow

1. 映射 FastAPI、鉴权、持久化、队列/状态、引擎、文件/音频、前端和 Docker 入口。
2. 分批检查并记录范围；优先越权、路径穿越、密钥、卡死/重复任务、SQLite/文件分歧、线程和 GPU 生命周期。
3. 按 P0/P1/P2/P3 报告触发条件、影响、文件/行号、证据和复现/推理。
4. 仅获授权后修复，并重跑最窄测试；跨层改动再运行 `run_tests.ps1`。
