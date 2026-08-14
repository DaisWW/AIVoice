#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/web/data /app/web/data/uploads /app/web/data/jobs /app/web/data/exports

required=(
  "/app/tools/GPT-SoVITS/GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/model.safetensors"
  "/app/tools/GPT-SoVITS/GPT_SoVITS/pretrained_models/chinese-hubert-base/model.safetensors"
  "/app/tools/GPT-SoVITS/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
  "/app/tools/GPT-SoVITS/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth"
  "/app/tools/GPT-SoVITS/GPT_SoVITS/text/G2PWModel/g2pW.onnx"
)

for path in "${required[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "[Voice Lab] missing model file: $path" >&2
    exit 20
  fi
done

echo "[Voice Lab] starting on ${VOICE_LAB_HOST:-0.0.0.0}:${VOICE_LAB_PORT:-18082}"
exec python -m uvicorn app.main:app \
  --app-dir /app/web \
  --host "${VOICE_LAB_HOST:-0.0.0.0}" \
  --port "${VOICE_LAB_PORT:-18082}" \
  --workers 1
