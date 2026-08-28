# Voice Lab Docker 部署

## 运行范围

容器只运行声音克隆，不包含降噪、变调、EQ、压缩、混响、响度统一或其他后期处理。生成结果直接保存为所选模型原音。

## 首次准备（只做一次）

先停止正在运行的本地 Voice Lab，再在工程根目录执行：

```powershell
cd C:\Workspace\Git\voice
.\.venv-gpt-sovits\Scripts\python.exe docker\prepare_data.py
```

脚本会创建 `docker-data`，把现有 `web\data` 复制为独立副本，并把 Windows 绝对路径改成容器路径。原 `web\data` 不会被修改。部署 BAT 不会调用此脚本，也不会自动复制、覆盖或删除数据。

如果确认要重新制作副本，先备份 `docker-data`，再显式执行：

```powershell
.\.venv-gpt-sovits\Scripts\python.exe docker\prepare_data.py --allow-existing
```

## 一键部署

双击工程根目录的 `docker_voice_lab.bat`，或执行：

```powershell
.\docker_voice_lab.bat
```

首次运行会生成 `docker\voice-lab.env` 中的随机管理员初始密码，并构建镜像。初始用户名为 `admin`；已有数据库若已经修改密码，env 中的值不会重置账户。只有首次生成 env 时 BAT 会显示初始密码，后续启动不会回显凭据。后续再次运行只执行幂等的 `compose up`，不会迁移数据；代码更新后使用：

```powershell
.\docker_voice_lab.bat build
```

常用命令：

```powershell
.\docker_voice_lab.bat status
.\docker_voice_lab.bat logs
.\docker_voice_lab.bat stop
```

管理后台 URL、初始用户名和（仅首次生成时的）初始密码会由 BAT 输出，浏览器打开后正常登录；管理员再从后台创建局域网成员账户。默认端口为 `18082`，可在 env 设置 `VOICE_LAB_PORT`，BAT 的健康检查和输出会跟随该端口。Windows 防火墙只建议放行实际配置的 TCP 端口。

文本模型也可通过 `VOICE_TEXT_MODEL_BASE_URL`、`VOICE_TEXT_MODEL_API_KEY` 和 `VOICE_TEXT_MODEL` 传入容器；管理员页面保存的配置优先用于后续请求。

## 镜像与模型

默认基础镜像固定为 CUDA 12.1 / PyTorch 2.5.1 / Torchaudio 2.5.1，并在当前 RTX 4070 环境实测，不使用 `latest`。Docker Hub 不通时可以在 `docker\voice-lab.env` 修改 `VOICE_LAB_BASE_IMAGE` 为可访问的同版本镜像源；替换源时不能改变 PyTorch/CUDA 组合。

依赖下载较慢时，可在同一 env 文件设置构建代理。宿主机代理若为 `127.0.0.1:7897`，容器内要使用：

```text
VOICE_LAB_BUILD_PROXY=http://host.docker.internal:7897
```

不需要代理时保持空值。该配置只用于构建镜像，不会写入最终运行环境。

如果所在网络无法访问默认 Python 包源，可设置 `VOICE_LAB_PYPI_INDEX` 为可访问的公开镜像；默认生成的 env 使用清华镜像。该配置只用于构建镜像，不会写入运行环境。

模型不会打进应用层镜像，Compose 以只读方式挂载：

```text
tools\GPT-SoVITS\GPT_SoVITS\pretrained_models
tools\GPT-SoVITS\GPT_SoVITS\text\G2PWModel
tools\CosyVoice
tools\Qwen3-TTS
tools\models
```

这样镜像更新不会重复占用模型空间，模型也可以独立备份。GPT-SoVITS 是基准模型；CosyVoice3 与 Qwen3-TTS 未安装时会在网页中禁用，不影响服务启动。

## 数据持久化

```text
docker-data/  -> /app/web/data   SQLite、上传、克隆任务和导出
input/        -> /app/input      旧声音库和台本（只读）
output/       -> /app/output     旧 CLI 结果（只读）
```

容器内只运行一个 Uvicorn worker 和一个 GPU 队列，避免重复加载模型导致显存耗尽。`docker-data` 是普通目录，停止或重建容器不会删除它。

## 验收

```powershell
docker compose --project-directory C:\Workspace\Git\voice `
  --env-file C:\Workspace\Git\voice\docker\voice-lab.env `
  -f C:\Workspace\Git\voice\docker\compose.yaml ps
```

状态应为 `healthy`，容器就绪探针 `/api/readyz` 返回 `ok: true`。该探针要求 GPU 队列存活且必需模型可用；可选模型缺失不会阻止容器启动。`/api/healthz` 仅表示 Web 进程存活。登录工作台后，`/api/health` 会显示完整模型与队列状态。正式验收再提交一条短台本生成，确认音频能播放和下载。
