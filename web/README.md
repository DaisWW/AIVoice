# Web 架构

`web/` 是局域网声音克隆服务。HTTP 层只编排请求，SQLite 仓储只处理持久化，单 GPU 队列串行调用当前声音模型。

```text
web/
├─ app/
│  ├─ api/
│  │  ├─ routes/                  页面、系统、声音、台本、任务、管理员路由
│  │  ├─ access.py                账户、项目成员和系统管理员访问规则
│  │  ├─ candidate_operations.py  单句重做、采用和试听
│  │  ├─ payloads.py              对外 JSON 结构
│  │  ├─ job_creation.py          单模型与同源多模型任务创建
│  │  ├─ uploads.py               台本与多格式录音上传
│  │  └─ downloads.py             单段、全部与正式导出
│  ├─ persistence/                SQLite 结构、连接与分域仓储
│  ├─ main.py                     FastAPI 应用工厂
│  ├─ services.py                 生命周期和运行资源
│  ├─ queue_worker.py             单 GPU 队列、进度和预计等待时间
│  ├─ engines/                    本地模型与 ElevenLabs/MiniMax 小样本克隆适配
│  ├─ engine_adapter.py           多模型引擎兼容入口
│  ├─ provider_config.py          管理员供应商配置与云端音色映射
│  ├─ audio_conversion.py         上传录音标准化为 WAV
│  ├─ audio_quality.py            参考录音质量提示
│  ├─ legacy_import.py            旧 input/output 幂等登记
│  └─ script_parser.py            台本和发音格式解析
├─ static/                        原生 HTML、CSS、ES Modules
├─ tests/                         API、队列、克隆输入、解析和兼容测试
├─ config/profiles.json           模型注册、预设和参数能力
└─ data/                          SQLite、上传、任务音频和导出缓存
```

依赖方向：

```text
浏览器 -> API routes -> ApplicationServices
                              ├-> persistence -> SQLite
                              ├-> JobQueue -> VoiceEngine -> 当前模型适配器
                              └-> LegacyImporter -> legacy repository
```

约束：

- 路由不直接写 SQL，也不直接调用声音模型。
- 只有 `JobQueue` 可调用 GPU；生产环境固定一个 Uvicorn worker。
- 可用模型按需加载；切换适配器或权重前释放上一模型显存。
- 上传录音统一经 PyAV 转为单声道、48 kHz、16-bit PCM WAV。
- 每个新生成结果只有一份模型原音，数据库的两个兼容路径字段指向同一文件。
- 历史 DSP 表和字段仅为已有数据库兼容保留，不提供仓储、队列、路由或前端入口。
- `LegacyImporter.run()` 可重复执行，不复制或删除旧音频。
- 前端使用原生 ES Modules，所有模块必须通过 `run_tests.ps1` 的递归语法检查。

## ElevenLabs 与 MiniMax 小样本克隆

系统管理员可在管理员配置页保存 ElevenLabs API Key、模型、WAV 输出格式和音色参数，并先执行连接检测。密钥保存在忽略版本控制的 `web/data/provider-settings.json`，普通接口与管理员读取接口都只返回“是否已配置”，不会返回密钥原文。

虫语台本支持 `raw:`、`ipa:` 或 `phoneme:` 前缀。前缀后的 IPA、组合音标、喉音和自定义符号不会经过中文音节表映射；普通 `mo-la` 格式仍保持旧行为。GPT-SoVITS 的中文文本前端会明确拒绝 raw 行，避免静默生成错误的中文读法。

选择 `ElevenLabs 小样本克隆` 后，任务首次使用某个声音库时会把当前启用、符合所选参考语气的标准化小样本上传到云端，创建 Instant Voice Clone 并缓存返回的 `voice_id`；同一参考音后续候选和批量任务会复用该音色。本地 GPT/Qwen 仍使用 3-10 秒短参考，云端克隆可利用声音库中的多条样本。参考音、API 账户或降噪设置变化时会重新注册，旧的云端音色需在 ElevenLabs 控制台按项目策略清理。

质量与使用约束：

- 参考录音必须已取得声音所有者的克隆及云端处理授权。
- 参考文本只有在能与录音逐字对应时才填写；虫语、怪叫或纯拟声无法可靠转写时应留空，不能按中文猜写。
- 默认输出 `wav_48000`；ElevenLabs 的高采样率 WAV 通常要求相应付费套餐，具体以账号权益为准。
- 连接检测只验证已保存密钥与 API 可达性；首次实际任务仍会消耗云端克隆/生成额度。
- 当前接入是克隆 TTS，不是驱动音频到目标音色的 speech-to-speech。需要保留怪叫、气息和表演节奏时，下一条独立流程应接入 Seed-VC 一类声音转换模型。

MiniMax 使用同一管理员配置页和 `/api/admin/providers/minimax` 接口。首次生成时，系统把声音库当前启用的标准化 WAV 按顺序合并后上传：有效人声至少 10 秒、最多 5 分钟、文件大小保守限制在 19 MiB 内；同一参考音和配置会缓存 `voice_id`，批量候选不会重复克隆。官方 T2A 输出采样率支持 8/16/22.05/24/32/44.1 kHz，质量优先可选 44.1 kHz；音量必须大于 0。MiniMax 克隆音色若连续 7 天未正式调用可能过期；修改样本或克隆相关配置会自动生成新的映射，长期停用时应按平台策略清理云端音色。
