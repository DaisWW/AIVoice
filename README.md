# Voice Lab

Voice Lab 是公司局域网使用的多模型声音克隆工具。它只负责根据真人声音模板生成角色台词原音，不做降噪、变调、共振峰、EQ、压缩、混响、响度统一或其他后期处理。

浏览器使用管理员创建的账户登录，声音库、台本和任务按项目隔离并在项目成员间共享；系统管理员可管理账户、项目、全部任务、审计记录与模型服务。所有 GPU 推理进入同一个队列，避免多人同时生成导致显存溢出。

## 启动

Windows 服务器可直接双击：

```text
start_voice_lab.bat
```

也可在 PowerShell 中运行：

```powershell
cd C:\Workspace\Git\voice
.\run_web.ps1
```

服务默认监听 `0.0.0.0:18082`：

- 本机工作台：<http://127.0.0.1:18082/>
- 本机管理员：<http://127.0.0.1:18082/admin>
- 局域网用户：`http://服务器局域网IP:18082/`

Windows 防火墙只需为 TCP `18082` 放行 `LocalSubnet`。生产环境保持单个 Uvicorn worker，否则每个进程都会重复加载一份 GPU 模型并拆散任务队列。

## 账户与管理员配置

首次启动会创建用户名 `admin` 的系统管理员。未设置环境变量时，初始密码写入 `web/data/bootstrap-admin.txt`；也可在首次启动前设置 `VOICE_LAB_ADMIN_PASSWORD`，兼容的 `VOICE_LAB_ADMIN_TOKEN` 会作为后备初始密码。已有数据库中的管理员密码不会因环境变量变化而被重置。

登录 <http://127.0.0.1:18082/admin> 后，可创建成员账户和项目，并在“模型服务”中配置 ElevenLabs 或 MiniMax。API Key 只保存在忽略版本控制的 `web/data/provider-settings.json`，管理接口也只返回是否已配置，不返回密钥原文。

## Docker

首次部署前，按需手工复制一次现有 Web 数据：

```powershell
.\.venv-gpt-sovits\Scripts\python.exe docker\prepare_data.py
```

然后运行：

```powershell
.\docker_voice_lab.bat
```

代码更新后重建：

```powershell
.\docker_voice_lab.bat build
```

容器会继续挂载 `docker-data`、`input`、`output` 和本机模型目录，不会把已有数据或模型打进镜像。详细说明见 [docker/README.md](docker/README.md)。

## 工作流程

```mermaid
flowchart LR
    A[选择或上传台本] --> B[选择声音库和参考语气]
    B --> C[选择主模型与可选对比模型]
    C --> D[进入单 GPU 队列]
    D --> E[准备 3-10 秒参考 WAV]
    E --> F[声音克隆模型原音生成]
    F --> G[每句生成 2 或 3 个原音候选]
    G --> H[试听 / 单句重做 / 采用]
    H --> I[单段下载 / 全部下载 / 正式导出]
```

参考录音在送入模型前会做必要的格式准备：解码、转单声道、重采样、裁剪首尾静音和防止削波。这些操作只作用于模型输入，不会对生成结果做声音后期。

生成结果由所选模型直接写为 16-bit PCM WAV。新任务中的 `audio_path` 与 `raw_audio_path` 指向同一个文件，不会额外复制所谓“成品”。

## Web 功能

- 新建任务可选择已有台本或上传 `.txt`、`.md`、`.csv`、`.docx`。
- 声音库支持 WAV、MP3、M4A、AAC、FLAC、OGG、OPUS，可上传多条真人录音并按参考语气分组。
- 新建任务提供小白/高级参数模式；可同时选择最多 4 个模型，按相同台本、声音库和候选种子创建对比任务。
- 每句生成 2 或 3 个候选；候选可试听、下载、采用，也可修改台词、发音、尾音方向和模型推理参数后重做。
- 页面任意时刻只播放一段音频，新播放会自动暂停上一段。
- “全部下载”下载当前任务每段的默认克隆音频；“正式导出”下载每段已采用候选及 `UnityAudioManifest.json`。
- 成员只能访问自己加入的项目；同一项目成员共享声音库、台本和任务。系统管理员后台可查看全局状态并管理账户与模型服务。

已存在的 SQLite、任务、上传和音频不会被删除。历史 DSP 文件仍保留在原目录中，但页面与 API 不再展示或创建调音版本；历史任务试听和下载优先使用其 GPT 原音路径。

## 台本格式

推荐一行一段，用竖线分隔显示标签和发音：

```text
陌生人，可否听我讲一段故事 | mo——la，na？↗
你肯定知道我爷爷的故事 | GU-la……gu-la-da。↘
```

也支持 Tab 分隔：

```text
显示标签<TAB>发音标记
```

CSV 必须包含 `text` 和 `pronunciation`，也兼容中文表头 `台词`、`发音`：

```csv
text,pronunciation
陌生人，可否听我讲一段故事,"mo——la，na？↗"
```

发音规则：

- `-` 只分隔音节。
- `↗` 和 `↘` 会转换为问句或陈述句标点，可能影响模型尾音，但生成存在随机性。
- 中文长横杠 `—` 与连续数量会保留在页面和下载清单中，供外部后期参考；送入模型前会移除，本工具不自动延音。
- 大写音节会保留为重音标记，供录音指导或外部后期参考，本工具不做重音 DSP。
- 未知拟声音节会在提交时返回具体行号。

虫语不是中文时可使用显式原音素模式，例如 `raw: t͡ʃa-ʀ——ɬa↗`（也支持 `ipa:` 和 `phoneme:`）。该模式保留 IPA、组合音标和自定义符号，不尝试映射成汉字；GPT-SoVITS 的中文文本前端不支持此模式，请选择能接受原音素的模型或使用真人表演转音色流程。

网页上传区可直接下载格式模板和完整发音说明。

## 声音模板

建议每条录音 3-15 秒，同一声音库准备 2-6 条：

- 近距离、无背景音乐、无明显房间混响。
- 一个文件只保留一个人的声音。
- 咬字、速度、气息和情绪尽量接近目标角色。
- 年龄感和表演情绪应优先由真人录制完成，后续再交给专业音频软件处理。

上传文件会统一保存为单声道、48 kHz、16-bit PCM WAV，原始文件名仍会显示在声音库中。单条录音最长 120 秒，超长素材应先拆分；实际模板仍建议每条 3-15 秒。

声音库可为每条录音保存逐字参考文本。只有文本能和录音严格对应时才填写；虫语、怪叫或纯拟声无法可靠转写时应留空，不要按中文猜写。

## 模型与 A/B 对比

`web/config/profiles.json` 注册了以下工作流：

- GPT-SoVITS V2 / V2ProPlus：提供稳定与表现预设，支持完整生成参数。
- CosyVoice3 0.5B：可选模型，适合拼音、音素与虫语台本，当前暴露语速。
- Qwen3-TTS 0.6B / 1.7B Base：可选声音克隆模型，支持随机性、候选范围和重复抑制。
- ElevenLabs 小样本克隆：管理员配置 API 后，首次使用会注册启用的参考样本并缓存云端 `voice_id`，后续批量候选复用该音色并输出 WAV。
- MiniMax 小样本克隆：管理员配置 API 后，首次使用会把启用样本按顺序合并为 10 秒至 5 分钟的 WAV，注册云端 `voice_id` 并在批量候选中复用；raw/IPA 台本会按 MiniMax 的括号音素格式发送。
- IndexTTS 2.5：仅展示评估状态，确认许可和完成接入前不可选择。
- Seed-VC：属于真人表演音频转音色，不进入当前台本 TTS 队列。

未安装的模型会显示原因并禁用，不会创建无法执行的任务。安装可选模型权重：

```powershell
.\code\download_optional_models.ps1 -Model qwen
.\code\download_optional_models.ps1 -Model cosy
```

多模型对比只让主模型使用页面参数，其他模型使用各自预设，避免把不兼容参数强行套用。填写基准种子可复现结果；留空时，多模型任务也会自动共享同一组随机种子。所有模型仍在同一个 GPU 队列中串行执行，切换模型时释放上一模型显存。

## 目录

```text
C:\Workspace\Git\voice
├─ code/
│  ├─ gpt_sovits_audio.py       音频解码、重采样和参考音构建
│  ├─ gpt_sovits_clone.py       GPT-SoVITS 模型加载、推理和原音输出
│  ├─ gpt_sovits_config.json    模型与参考音配置
│  ├─ download_gpt_sovits_models.ps1
│  └─ download_optional_models.ps1
├─ voice_core/                  发音解析与标记规则
├─ web/
│  ├─ app/                      FastAPI、队列、持久化和多模型引擎适配
│  ├─ static/                   Web 页面
│  ├─ config/profiles.json      模型注册、预设和参数能力
│  └─ data/                     SQLite、上传、任务和导出
├─ docker/                      Docker 构建、部署与数据准备
├─ input/                       旧输入，仅用于幂等登记
├─ output/                      旧输出，仅作为历史数据
├─ run_web.ps1                  PowerShell 启动入口
├─ start_voice_lab.bat          Windows 双击启动入口
└─ run_tests.ps1                后端与前端检查
```

新任务音频目录：

```text
web/data/jobs/<job-id>/
├─ reference.wav
├─ audio/001.wav
└─ candidates/001/<candidate-id>/audio.wav
```

## 测试

```powershell
cd C:\Workspace\Git\voice
.\run_tests.ps1
```

测试使用临时 SQLite 和假声音引擎，不加载 GPU，也不会修改 `input`、`output`、`web/data` 或 `docker-data`。
