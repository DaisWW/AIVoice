# Web 架构

`web/` 是局域网声音克隆服务。HTTP 层只编排请求，SQLite 仓储只处理持久化，单 GPU 队列串行调用 GPT-SoVITS。

```text
web/
├─ app/
│  ├─ api/
│  │  ├─ routes/                  页面、系统、声音、台本、任务、管理员路由
│  │  ├─ access.py                匿名用户、本机管理员和资源访问规则
│  │  ├─ candidate_operations.py  单句重做、采用和试听
│  │  ├─ payloads.py              对外 JSON 结构
│  │  ├─ uploads.py               台本与多格式录音上传
│  │  └─ downloads.py             单段、全部与正式导出
│  ├─ persistence/                SQLite 结构、连接与分域仓储
│  ├─ main.py                     FastAPI 应用工厂
│  ├─ services.py                 生命周期和运行资源
│  ├─ queue_worker.py             单 GPU 队列、进度和预计等待时间
│  ├─ engine_adapter.py           GPT-SoVITS 克隆适配
│  ├─ audio_conversion.py         上传录音标准化为 WAV
│  ├─ audio_quality.py            参考录音质量提示
│  ├─ legacy_import.py            旧 input/output 幂等登记
│  └─ script_parser.py            台本和发音格式解析
├─ static/                        原生 HTML、CSS、ES Modules
├─ tests/                         API、队列、克隆输入、解析和兼容测试
├─ config/profiles.json           GPT-SoVITS 推理档位
└─ data/                          SQLite、上传、任务音频和导出缓存
```

依赖方向：

```text
浏览器 -> API routes -> ApplicationServices
                              ├-> persistence -> SQLite
                              ├-> JobQueue -> VoiceEngine -> GPT-SoVITS
                              └-> LegacyImporter -> legacy repository
```

约束：

- 路由不直接写 SQL，也不直接调用 GPT-SoVITS。
- 只有 `JobQueue` 可调用 GPU；生产环境固定一个 Uvicorn worker。
- 上传录音统一经 PyAV 转为单声道、48 kHz、16-bit PCM WAV。
- 新生成结果只有一份 GPT 原音，数据库的两个兼容路径字段指向同一文件。
- 历史 DSP 表和字段仅为已有数据库兼容保留，不提供仓储、队列、路由或前端入口。
- `LegacyImporter.run()` 可重复执行，不复制或删除旧音频。
- 前端使用原生 ES Modules，所有模块必须通过 `run_tests.ps1` 的递归语法检查。
