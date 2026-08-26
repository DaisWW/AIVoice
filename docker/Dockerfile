ARG BASE_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime
FROM ${BASE_IMAGE}
ARG TORCH_VERSION=2.5.1
ARG TORCH_CUDA_INDEX=cu121
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

LABEL org.opencontainers.image.title="Voice Lab"
LABEL org.opencontainers.image.description="LAN multi-model voice cloning workspace"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/code:/app/tools/GPT-SoVITS:/app/tools/CosyVoice:/app/tools/CosyVoice/third_party/Matcha-TTS:/app/tools/Qwen3-TTS \
    HF_HOME=/app/web/data/cache/huggingface

WORKDIR /app

COPY code/requirements-gpt-sovits.txt /tmp/requirements-gpt-sovits.txt
COPY code/requirements-multimodel.txt /tmp/requirements-multimodel.txt
COPY web/requirements-web.txt /tmp/requirements-web.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends build-essential sox \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-deps \
        --index-url "https://download.pytorch.org/whl/${TORCH_CUDA_INDEX}" \
        "torchaudio==${TORCH_VERSION}+${TORCH_CUDA_INDEX}" \
    && python -m pip install --no-build-isolation --no-deps \
        --index-url "${PYPI_INDEX_URL}" "openai-whisper==20250625" \
    && python -m pip install --index-url "${PYPI_INDEX_URL}" \
        -r /tmp/requirements-gpt-sovits.txt -r /tmp/requirements-multimodel.txt -r /tmp/requirements-web.txt \
    && sox --version \
    && python -c "import accelerate, conformer, diffusers, hyperpyyaml, inflect, jieba_fast, matplotlib, omegaconf, sox, torch, torchaudio, transformers, whisper; expected='${TORCH_VERSION}'; assert torch.__version__.startswith(expected), (torch.__version__, expected); assert torchaudio.__version__.startswith(expected), (torchaudio.__version__, expected); print('Torch', torch.__version__, 'CUDA', torch.version.cuda); print('TorchAudio', torchaudio.__version__); print('Transformers', transformers.__version__); print('Multi-model runtime imports OK')" \
    && apt-get purge -y --auto-remove build-essential

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" voicelab \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin voicelab

COPY . /app
COPY docker/entrypoint.sh /usr/local/bin/voice-lab-entrypoint
RUN python -m pip uninstall -y praat-parselmouth pyloudnorm \
    && chmod 755 /usr/local/bin/voice-lab-entrypoint \
    && python -m compileall -q /app/code /app/voice_core /app/web/app

EXPOSE 18082

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18082/api/healthz', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/voice-lab-entrypoint"]
