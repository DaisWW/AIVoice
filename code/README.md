# 声音克隆内核

`code/` 只保留 GPT-SoVITS V2 声音克隆运行代码：

```text
gpt_sovits_audio.py              音频解码、重采样与参考音构建
gpt_sovits_clone.py              GPT-SoVITS 模型检查、加载与推理
gpt_sovits_config.json           参考音与 GPT-SoVITS 基础配置
download_gpt_sovits_models.ps1   模型下载、续传与文件校验
requirements-gpt-sovits.txt      推理环境依赖
```

生成音频不会经过降噪、变调、共振峰、滤波、压缩、混响、响度统一或延音处理。参考录音的解码、单声道转换、重采样、静音裁剪和防削波仅用于满足模型输入要求。

依赖清单中的 `praat-parselmouth` 与 `pyloudnorm` 仅用于命中本机已验证的 Docker 重型依赖缓存；`docker/Dockerfile` 会在最终镜像层显式卸载它们，业务代码没有对应导入。

Web 运行入口在 `web/app/engine_adapter.py`，服务启动方式见根目录 `README.md`。
