ARG BASE_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime
FROM ${BASE_IMAGE}
ARG TORCH_VERSION=2.5.1
ARG TORCH_CUDA_INDEX=cu121

LABEL org.opencontainers.image.title="Voice Lab"
LABEL org.opencontainers.image.description="LAN voice generation and post-processing workspace"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/code:/app/tools/GPT-SoVITS \
    HF_HOME=/app/web/data/cache/huggingface

WORKDIR /app

COPY code/requirements-gpt-sovits.txt /tmp/requirements-gpt-sovits.txt
COPY web/requirements-web.txt /tmp/requirements-web.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-deps \
        --index-url "https://download.pytorch.org/whl/${TORCH_CUDA_INDEX}" \
        "torchaudio==${TORCH_VERSION}+${TORCH_CUDA_INDEX}" \
    && python -m pip install -r /tmp/requirements-gpt-sovits.txt -r /tmp/requirements-web.txt \
    && python -c "import jieba_fast, matplotlib, torch, torchaudio; expected='${TORCH_VERSION}'; assert torch.__version__.startswith(expected), (torch.__version__, expected); assert torchaudio.__version__.startswith(expected), (torchaudio.__version__, expected); print('Torch', torch.__version__, 'CUDA', torch.version.cuda); print('TorchAudio', torchaudio.__version__); print('Runtime imports OK')" \
    && apt-get purge -y --auto-remove build-essential

COPY . /app
COPY docker/entrypoint.sh /usr/local/bin/voice-lab-entrypoint
RUN python -m pip uninstall -y praat-parselmouth pyloudnorm \
    && chmod 755 /usr/local/bin/voice-lab-entrypoint \
    && python -m compileall -q /app/code /app/voice_core /app/web/app

LABEL org.opencontainers.image.description="LAN GPT-SoVITS voice cloning workspace"

EXPOSE 18082

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18082/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/voice-lab-entrypoint"]
